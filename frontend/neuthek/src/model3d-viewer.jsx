// 3D model viewer — interactive WebGL preview for STL / OBJ / glTF /
// GLB files, with an optional source-code pane. Loads the served
// original bytes (the same authed `fetchMediaBlob(originalMediaUrl(id))`
// path the CSV / ICS / VCF viewers use), parses them with the matching
// three.js loader, drops the result into a scene with a soft studio
// look (gradient/vignette backdrop, environment-lit PBR surface, a
// subtle contact shadow on the ground), auto-fits the camera to the
// model's bounding sphere, and wires buttery damped OrbitControls for
// rotate / zoom / pan.
//
// Two panes behind a segmented toggle (Render | Source), default
// Render:
//   - Render: the live WebGL canvas with a floating toolbar (format
//     badge, triangle / vertex / object counts, reset-camera,
//     wireframe toggle, auto-rotate toggle).
//   - Source: the raw file text rendered by the shared <CodeView>
//     (STL ascii + OBJ are text; .gltf is JSON text). Binary STL / GLB
//     have no meaningful text, so the Source pane shows a short note
//     instead.
// The canvas stays mounted across the toggle (hidden via CSS, not
// unmounted) so flipping to Source and back never tears down / rebuilds
// the GL context. Returning to Render re-measures the now-visible canvas
// and re-frames the camera.
//
// Why ArrayBuffer + loader.parse instead of loader.load(url): the
// served bytes sit behind cookie / Bearer auth, so a bare
// loader.load(url) (which does its own un-authed fetch) would 401.
// We fetch the blob ourselves through the shared helper and hand the
// parser an ArrayBuffer (STL / glTF) or text (OBJ). For glTF we point
// the loader's resource path at a blob: URL so any *external* buffers
// or textures referenced by a .gltf resolve — self-contained .glb
// files embed everything and don't need it.
//
// Resource hygiene: on unmount (or file change) we stop the RAF loop,
// dispose every geometry / material / texture in the scene graph,
// dispose the environment render-target / PMREM, dispose OrbitControls
// + the WebGLRenderer, and detach the canvas. Without the
// renderer.dispose() + forceContextLoss() the browser leaks a WebGL
// context per opened model and eventually refuses to create new ones
// ("too many active WebGL contexts").
import React, { useState, useEffect, useRef, useCallback } from "react";
import toast from "react-hot-toast";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { fetchMediaBlob, originalMediaUrl } from "@/api/files";
import { CodeView } from "./code-preview.jsx";
import { Icon } from "./icons.jsx";

// Whether the user has asked the OS to minimize motion. Read once at
// module scope (it rarely changes mid-session) — gates auto-rotate so we
// don't spin the model for users who opted out.
const PREFERS_REDUCED_MOTION =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Normalize the extension off the filename (the catalog kind already
// gated us here, but we still need the *specific* format to pick a
// loader). Falls back to sniffing the passed `ext` prop.
function pickFormat(fileName, ext) {
  const fromName = (fileName || "").toLowerCase().match(/\.([a-z0-9]+)$/)?.[1];
  const e = (ext || fromName || "").toLowerCase().replace(/^\./, "");
  if (e === "stl" || e === "obj" || e === "gltf" || e === "glb") return e;
  return null;
}

// Decide whether an STL ArrayBuffer is binary. Mirrors three.js's own
// STLLoader heuristic: a binary STL is an 80-byte header + a uint32
// triangle count + 50 bytes per triangle, so the total length is
// exactly 84 + 50·n. If the arithmetic lines up we treat it as binary.
// As a fallback (some exporters pad the file) we also bail to "binary"
// when the leading bytes aren't the ascii "solid" sentinel — but the
// length check is authoritative and handles the corner case of an
// ascii file that happens to begin with "solid…" yet is really binary.
function isBinarySTL(buffer) {
  if (!buffer || buffer.byteLength < 84) return false;
  const reader = new DataView(buffer);
  const faces = reader.getUint32(80, true);
  const expect = 80 + 4 + 50 * faces;
  if (expect === buffer.byteLength) return true;
  // Length didn't match a binary layout — sniff the first 5 bytes for
  // the ascii "solid" magic. Absence of it strongly implies binary.
  const head = new Uint8Array(buffer, 0, Math.min(5, buffer.byteLength));
  const magic = String.fromCharCode(...head).toLowerCase();
  return magic !== "solid";
}

// Frame the camera on the loaded object via its bounding SPHERE (not
// just the box): recenter the model at the world origin, point the
// controls target there, and back the camera off along a fixed diagonal
// by enough distance that the sphere fits the vertical FOV. Mirrors the
// canonical three.js "fit camera to object" recipe.
//
// Two things here kill the "model vanishes at certain rotation angles"
// bug:
//   1. near/far are derived from the sphere RADIUS (near ≈ radius/100,
//      far ≈ radius*100 from the camera) — wide enough that no part of
//      the model is ever clipped by the z-planes as it spins, tight
//      enough to preserve depth precision on tiny or huge models.
//   2. we recenter the model at the origin first, so the orbit pivot
//      and the geometry share an origin and the bounds can't drift out
//      of the frustum.
// Returns the model's bounding-sphere radius so the caller can size a
// matching ground/contact shadow.
function frameObject(object, camera, controls, recenter = false) {
  const box = new THREE.Box3().setFromObject(object);
  if (box.isEmpty()) return 0;
  const center = box.getCenter(new THREE.Vector3());

  // Recenter the model at the world origin ONCE (on first load). Shifting
  // the object so its bounding-box center sits at (0,0,0) keeps the orbit
  // pivot, the geometry, and the frustum all sharing an origin — the
  // simplest guard against bounds drifting out of view at extreme angles.
  if (recenter) {
    object.position.sub(center);
    object.updateMatrixWorld(true);
    box.setFromObject(object);
    box.getCenter(center); // now ≈ origin
  }

  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const radius = sphere.radius || (box.getSize(new THREE.Vector3()).length() / 2) || 1;
  const fov = (camera.fov * Math.PI) / 180;
  // Distance at which a sphere of `radius` exactly fills the vertical
  // FOV, then 1.4× breathing room so the model doesn't kiss the edges.
  const fitDist = (radius / Math.sin(fov / 2)) * 1.4;
  const dir = new THREE.Vector3(1, 0.7, 1).normalize();
  camera.position.copy(sphere.center).addScaledVector(dir, fitDist);
  // Clip planes keyed to the model size — never clip the model as it
  // rotates (far), keep z-precision on small models (near). radius/100
  // and radius*100 give a 4-decade depth range centered on the model.
  camera.near = Math.max(radius / 100, 1e-4);
  camera.far = radius * 100 + fitDist;
  camera.updateProjectionMatrix();
  controls.target.copy(sphere.center);
  controls.minDistance = radius * 0.05;
  controls.maxDistance = radius * 100;
  controls.update();
  return radius;
}

// Walk a freshly-loaded scene and make every mesh render correctly from
// any angle and under any light:
//   - side = THREE.DoubleSide so open / non-manifold / inward-facing
//     meshes show their far wall instead of being back-face culled
//     (the "only one side renders" bug). Applied to BOTH our default
//     material and any materials a glTF/OBJ shipped.
//   - computeVertexNormals() when a mesh has positions but no normals,
//     so the standard material actually shades (flat unlit otherwise).
//   - computeBoundingBox() + computeBoundingSphere() so three's
//     frustum-culling test uses a CORRECT sphere — a stale/!null sphere
//     from a loader (or one left over after we edited the geometry) is
//     the other half of the "disappears at angles" bug.
// Returns { triangles, vertices, objects, meshes } for the meta readout
// + the wireframe toggle.
function prepareModel(object) {
  let triangles = 0;
  let vertices = 0;
  let objects = 0;
  const meshes = [];
  object.traverse((child) => {
    if (!child.isMesh) return;
    objects += 1;
    meshes.push(child);
    const g = child.geometry;
    if (g) {
      if (!g.attributes.normal && g.attributes.position) g.computeVertexNormals();
      // Recompute bounds from the (possibly normal-augmented) geometry so
      // frustum culling can't wrongly hide the mesh at some orientations.
      g.computeBoundingBox();
      g.computeBoundingSphere();
      const pos = g.attributes.position;
      const idx = g.index;
      triangles += idx ? idx.count / 3 : (pos ? pos.count / 3 : 0);
      vertices += pos ? pos.count : 0;
    }
    if (!child.material) child.material = defaultMaterial();
    // Force two-sided rendering on whatever material(s) this mesh has so
    // every face is visible + lit from either side.
    const mats = Array.isArray(child.material) ? child.material : [child.material];
    for (const m of mats) {
      if (!m) continue;
      m.side = THREE.DoubleSide;
      m.needsUpdate = true;
    }
  });
  return { triangles: Math.round(triangles), vertices, objects, meshes };
}

// Default material for geometry-only formats (STL, and OBJ files that
// shipped no .mtl). A mid-grey physically-based surface that picks up
// the environment map for soft, believable highlights without looking
// flat or chromey. DoubleSide so open meshes don't show a hole when the
// camera sees their inner wall.
function defaultMaterial() {
  return new THREE.MeshStandardMaterial({
    color: 0x9aa4b2,
    metalness: 0.15,
    roughness: 0.55,
    envMapIntensity: 0.9,
    flatShading: false,
    side: THREE.DoubleSide,
  });
}

// Map our format token to a MIME the CodeView's grammar picker likes.
// STL/OBJ have no dedicated Prism grammar (rendered as monospace, which
// is the right call); .gltf is JSON.
function sourceMime(format) {
  if (format === "gltf") return "application/json";
  return "text/plain";
}

// A soft round "contact shadow" sprite laid flat under the model. We
// draw a radial-gradient blob into a tiny canvas once and map it onto a
// ground plane — cheaper and softer than a real shadow map, and it
// grounds the model so it doesn't float. Returns { mesh, texture } so
// the caller can add + later dispose both.
function makeContactShadow(radius) {
  const size = 256;
  const c = document.createElement("canvas");
  c.width = c.height = size;
  const ctx = c.getContext("2d");
  const g = ctx.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
  g.addColorStop(0, "rgba(0,0,0,0.42)");
  g.addColorStop(0.55, "rgba(0,0,0,0.16)");
  g.addColorStop(1, "rgba(0,0,0,0)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, size, size);
  const texture = new THREE.CanvasTexture(c);
  texture.colorSpace = THREE.SRGBColorSpace;
  const geo = new THREE.PlaneGeometry(radius * 3.4, radius * 3.4);
  const mat = new THREE.MeshBasicMaterial({
    map: texture,
    transparent: true,
    depthWrite: false,
    opacity: 0.9,
  });
  const mesh = new THREE.Mesh(geo, mat);
  mesh.rotation.x = -Math.PI / 2;
  mesh.renderOrder = -1;
  return { mesh, texture };
}

export function Model3dViewer({ fileId, fileName, fileExt }) {
  const mountRef = useRef(null);
  // Holds the long-lived three.js objects + the latest resize handle so
  // the toolbar actions / cleanup can reach them outside the load effect.
  const stateRef = useRef({});
  const [status, setStatus] = useState("loading"); // loading | ready | error
  const [error, setError] = useState(null);
  const [meta, setMeta] = useState(null); // { format, triangles, vertices, objects }
  // Decoded source for the Source pane: { text, mime } when the file is
  // text-ish (STL ascii / OBJ / .gltf), or { note } when it's binary
  // (binary STL / GLB) and there's nothing useful to show as text.
  const [source, setSource] = useState(null);
  const [view, setView] = useState("render"); // render | source
  const [wireframe, setWireframe] = useState(false);
  // Auto-rotate spins the model gently until the first interaction, as a
  // "this is 3D, you can grab it" cue. Off from the start when the user
  // prefers reduced motion.
  const [autoRotate, setAutoRotate] = useState(!PREFERS_REDUCED_MOTION);
  // Hint pill fades out after the first drag/scroll.
  const [interacted, setInteracted] = useState(false);

  const format = pickFormat(fileName, fileExt);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    if (!format) {
      setStatus("error");
      setError("Unsupported 3D format.");
      return;
    }

    let disposed = false;
    let rafId = 0;
    let ro = null;
    const objectUrls = [];

    // --- renderer / scene / camera / lights -------------------------
    // The mount can briefly measure 0×0 (modal still laying out); fall
    // back to a sane size and let the ResizeObserver correct + re-frame
    // the moment a real box arrives.
    const width = mount.clientWidth || 640;
    const height = mount.clientHeight || 480;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    // ACES gives the PBR highlights a filmic roll-off so bright spots
    // don't clip harshly — reads more "studio render", less "raw GL".
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    // Make the canvas behave as a block that fills its container even
    // before the stylesheet loads (defensive — styles-model3d.css also
    // sets this).
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 2000);
    camera.position.set(0, 0, 5);

    // Image-based lighting: bake a neutral soft-studio environment with
    // PMREM and use it as the scene's envMap so PBR surfaces get smooth,
    // believable reflections + ambient occlusion-ish shading for free.
    const pmrem = new THREE.PMREMGenerator(renderer);
    const envRT = pmrem.fromScene(new RoomEnvironment(), 0.04);
    scene.environment = envRT.texture;

    // Multi-direction rig on top of the IBL so EVERY face is lit no
    // matter how the model is turned (the lone key/back pair left faces
    // pointing away from both in shadow). Ambient floor + hemisphere for
    // even base fill, then key / fill / back / bottom directionals from
    // four directions so silhouette edges and contours still pop.
    const ambient = new THREE.AmbientLight(0xffffff, 0.25);
    scene.add(ambient);
    const hemi = new THREE.HemisphereLight(0xffffff, 0x40454f, 0.45);
    scene.add(hemi);
    const key = new THREE.DirectionalLight(0xffffff, 0.85);
    key.position.set(3, 5, 4);
    scene.add(key);
    const fill = new THREE.DirectionalLight(0xffffff, 0.45); // opposite the key
    fill.position.set(-5, 2, 4);
    scene.add(fill);
    const back = new THREE.DirectionalLight(0xbfd4ff, 0.4); // cool back/rim light
    back.position.set(-4, 2, -5);
    scene.add(back);
    const under = new THREE.DirectionalLight(0xffffff, 0.25); // lifts down-facing faces
    under.position.set(0, -5, 0);
    scene.add(under);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;     // buttery, settles smoothly
    controls.rotateSpeed = 0.85;
    controls.panSpeed = 0.8;
    controls.zoomSpeed = 0.85;
    controls.autoRotate = !PREFERS_REDUCED_MOTION;
    controls.autoRotateSpeed = 0.9;    // a slow, calm turn
    // First user interaction stops the auto-rotate and fades the hint.
    const onFirstInteract = () => {
      controls.autoRotate = false;
      setAutoRotate(false);
      setInteracted(true);
    };
    controls.addEventListener("start", onFirstInteract);

    // --- responsive resize -----------------------------------------
    // Re-sync the drawing buffer + camera aspect to the live container
    // box. Guarded against the 0×0 readings a hidden (display:none)
    // container reports so we never divide by zero or shrink to nothing.
    const onResize = () => {
      const w = mount.clientWidth;
      const h = mount.clientHeight;
      if (!w || !h) return; // hidden (Source pane active) or not laid out yet
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    stateRef.current = { renderer, scene, camera, controls, onResize, mount, pmrem, envRT };

    // --- render loop ------------------------------------------------
    const animate = () => {
      if (disposed) return;
      rafId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(onResize);
      ro.observe(mount);
    } else {
      window.addEventListener("resize", onResize);
    }

    // Add a parsed model to the scene: count triangles + vertices for the
    // meta readout, lay a soft contact shadow under it, frame the camera,
    // flip to "ready". `onResize()` first so the camera aspect matches
    // the real container before we fit (the initial size may have been
    // the 640×480 fallback).
    const addModel = (object) => {
      if (disposed) return;
      if (!object) {
        setStatus("error");
        setError("Model contained no renderable scene.");
        return;
      }
      // Normalize the scene: DoubleSide on every material, vertex normals
      // + fresh bounding volumes on every geometry, and a triangle/vertex
      // tally for the meta readout.
      const { triangles, vertices, objects, meshes } = prepareModel(object);
      // Remember the meshes so the wireframe toggle can flip every
      // material at once.
      stateRef.current.meshes = meshes;
      scene.add(object);
      // Remember the model root so reset-camera can re-fit *it* (not the
      // whole scene, which would include the larger ground shadow).
      stateRef.current.model = object;
      onResize();
      // Recenter at the origin + fit the camera to the bounding sphere.
      const radius = frameObject(object, camera, controls, true);
      // Soft contact shadow dropped to the bottom of the (now recentered)
      // bounding box so the model reads as resting on a surface, not
      // floating.
      if (radius > 0) {
        const box = new THREE.Box3().setFromObject(object);
        const center = box.getCenter(new THREE.Vector3());
        const { mesh: shadow, texture: shadowTex } = makeContactShadow(radius);
        shadow.position.set(center.x, box.min.y + radius * 0.001, center.z);
        scene.add(shadow);
        stateRef.current.shadow = shadow;
        stateRef.current.shadowTex = shadowTex;
      }
      setMeta({ format, triangles, vertices, objects });
      setStatus("ready");
    };

    (async () => {
      try {
        const blob = await fetchMediaBlob(originalMediaUrl(fileId));
        if (disposed) return;

        if (format === "stl") {
          const buf = await blob.arrayBuffer();
          if (disposed) return;
          const geometry = new STLLoader().parse(buf);
          geometry.computeVertexNormals();
          const mesh = new THREE.Mesh(geometry, defaultMaterial());
          addModel(mesh);
          // Source pane: ascii STL is human-readable text; binary STL is
          // not, so just describe it.
          if (isBinarySTL(buf)) {
            setSource({ note: `Binary STL · ${(buf.byteLength / 1024).toFixed(1)} KB · ${Math.round(geometry.attributes.position.count / 3).toLocaleString()} triangles. No text source to show.` });
          } else {
            setSource({ text: new TextDecoder("utf-8", { fatal: false }).decode(buf), mime: "text/plain" });
          }
        } else if (format === "obj") {
          const text = await blob.text();
          if (disposed) return;
          const group = new OBJLoader().parse(text);
          // OBJLoader leaves a default un-lit material; swap in ours so
          // the mesh actually shades under the lights.
          group.traverse((c) => { if (c.isMesh) c.material = defaultMaterial(); });
          addModel(group);
          setSource({ text, mime: "text/plain" });
        } else {
          // gltf / glb — parse from ArrayBuffer. Point resourcePath at a
          // blob URL so a .gltf that references sibling .bin / textures
          // can resolve them (relative to the original); .glb embeds
          // everything so the path is harmless there.
          const buf = await blob.arrayBuffer();
          if (disposed) return;
          const blobUrl = URL.createObjectURL(blob);
          objectUrls.push(blobUrl);
          // A .gltf is JSON text; a .glb is binary (magic 'glTF' =
          // 0x46546C67 little-endian at offset 0). Show the JSON for
          // .gltf, a note for .glb.
          const isGlb = format === "glb" ||
            (buf.byteLength >= 4 && new DataView(buf).getUint32(0, true) === 0x46546c67);
          if (isGlb) {
            setSource({ note: `Binary glTF (GLB) · ${(buf.byteLength / 1024).toFixed(1)} KB. Geometry, materials and textures are packed in binary — no text source to show.` });
          } else {
            setSource({ text: new TextDecoder("utf-8", { fatal: false }).decode(buf), mime: "application/json" });
          }
          const loader = new GLTFLoader();
          loader.parse(
            buf,
            blobUrl.substring(0, blobUrl.lastIndexOf("/") + 1),
            (gltf) => addModel(gltf.scene || gltf.scenes?.[0]),
            (err) => {
              if (disposed) return;
              setStatus("error");
              setError(err?.message || "Could not parse model");
              toast.error("Couldn't load 3D model");
            },
          );
        }
      } catch (e) {
        if (disposed) return;
        setStatus("error");
        setError(e?.message || "Could not load file");
        toast.error("Couldn't load 3D model");
      }
    })();

    // --- teardown ---------------------------------------------------
    return () => {
      disposed = true;
      if (rafId) cancelAnimationFrame(rafId);
      if (ro) ro.disconnect();
      else window.removeEventListener("resize", onResize);
      controls.removeEventListener("start", onFirstInteract);
      // Dispose every geometry / material / texture in the graph
      // (includes the contact-shadow plane + its canvas texture).
      scene.traverse((obj) => {
        if (obj.geometry) obj.geometry.dispose?.();
        const mat = obj.material;
        if (mat) {
          const mats = Array.isArray(mat) ? mat : [mat];
          for (const m of mats) {
            for (const k in m) {
              const v = m[k];
              if (v && v.isTexture) v.dispose?.();
            }
            m.dispose?.();
          }
        }
      });
      // The baked environment render target + PMREM generator hold GPU
      // memory of their own — release them explicitly.
      envRT?.dispose?.();
      pmrem?.dispose?.();
      controls.dispose();
      renderer.dispose();
      // Force the GL context to release immediately — without this the
      // context lingers until GC and the browser caps total live
      // contexts, blanking later viewers.
      renderer.forceContextLoss?.();
      if (renderer.domElement && renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
      for (const u of objectUrls) URL.revokeObjectURL(u);
      stateRef.current = {};
    };
  }, [fileId, fileName, fileExt, format]);

  // Reset transient view state when the file changes so a new model
  // opens on the Render pane (and auto-rotates again unless the user
  // prefers reduced motion).
  useEffect(() => {
    setStatus("loading");
    setError(null);
    setMeta(null);
    setSource(null);
    setView("render");
    setWireframe(false);
    setAutoRotate(!PREFERS_REDUCED_MOTION);
    setInteracted(false);
  }, [fileId]);

  // When the toggle returns to Render, the canvas container went from
  // display:none back to visible — re-measure it on the next frame so
  // the drawing buffer matches the (previously 0×0) box. The RAF loop
  // keeps painting, so a resize is all that's needed.
  useEffect(() => {
    if (view !== "render") return;
    const st = stateRef.current;
    if (!st.onResize) return;
    const id = requestAnimationFrame(() => st.onResize && st.onResize());
    return () => cancelAnimationFrame(id);
  }, [view]);

  // Push the wireframe flag onto every mesh material whenever it changes
  // (or once a model finishes loading while the flag is already set).
  useEffect(() => {
    const meshes = stateRef.current.meshes;
    if (!meshes) return;
    for (const m of meshes) {
      const mats = Array.isArray(m.material) ? m.material : [m.material];
      for (const mat of mats) if (mat) mat.wireframe = wireframe;
    }
  }, [wireframe, status]);

  // Reset camera back to the framed default for the current model. Fit
  // against the model root specifically — framing the whole scene would
  // include the ground shadow (larger than the model) and zoom too far
  // out.
  const resetCamera = useCallback(() => {
    const st = stateRef.current;
    if (!st.model || !st.camera || !st.controls) return;
    frameObject(st.model, st.camera, st.controls);
  }, []);

  // Toolbar toggle: flip auto-rotate on/off live.
  const toggleAutoRotate = useCallback(() => {
    setAutoRotate((on) => {
      const next = !on;
      const st = stateRef.current;
      if (st.controls) st.controls.autoRotate = next;
      return next;
    });
  }, []);

  const canShowSource = !!source && (source.text != null || source.note != null);
  const hasText = !!source && source.text != null;

  return (
    <div className="model3d" onClick={(e) => e.stopPropagation()}>
      {/* ---- toolbar ---- */}
      <div className="model3d__bar">
        <div className="model3d__meta">
          <span className="model3d__name mono">{fileName}</span>
          {meta && <span className="model3d__badge">{meta.format}</span>}
          {meta && (meta.triangles > 0 || meta.vertices > 0) && (
            <span className="model3d__stats">
              {meta.triangles > 0 && (
                <span className="model3d__stat">{meta.triangles.toLocaleString()} tris</span>
              )}
              {meta.vertices > 0 && (
                <span className="model3d__stat">{meta.vertices.toLocaleString()} verts</span>
              )}
              {meta.objects > 1 && (
                <span className="model3d__stat">{meta.objects.toLocaleString()} objects</span>
              )}
            </span>
          )}
        </div>

        <div className="model3d__actions">
          {view === "render" && (
            <>
              <button
                type="button"
                className="model3d__tool"
                title="Reset camera"
                aria-label="Reset camera"
                onClick={resetCamera}
                disabled={status !== "ready"}
              >
                <Icon name="refresh" size={14} />
              </button>
              <button
                type="button"
                className="model3d__tool"
                data-on={wireframe ? "true" : "false"}
                aria-pressed={wireframe}
                title={wireframe ? "Solid shading" : "Wireframe"}
                aria-label="Toggle wireframe"
                onClick={() => setWireframe((v) => !v)}
                disabled={status !== "ready"}
              >
                <Icon name="cube" size={14} />
              </button>
              <button
                type="button"
                className="model3d__tool"
                data-on={autoRotate ? "true" : "false"}
                aria-pressed={autoRotate}
                title={autoRotate ? "Stop auto-rotate" : "Auto-rotate"}
                aria-label="Toggle auto-rotate"
                onClick={toggleAutoRotate}
                disabled={status !== "ready"}
              >
                <Icon name={autoRotate ? "pause" : "play"} size={14} />
              </button>
              <span className="model3d__divider" aria-hidden="true" />
            </>
          )}

          {/* Render / Source segmented toggle. */}
          <div className="model3d__seg" role="tablist" aria-label="View mode">
            <button
              type="button"
              role="tab"
              aria-selected={view === "render"}
              className="model3d__segbtn"
              data-active={view === "render" ? "true" : "false"}
              onClick={() => setView("render")}
            >
              <Icon name="cube" size={13} />
              <span>Render</span>
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "source"}
              className="model3d__segbtn"
              data-active={view === "source" ? "true" : "false"}
              onClick={() => setView("source")}
            >
              <Icon name="code" size={13} />
              <span>Source</span>
            </button>
          </div>
        </div>
      </div>

      {/* ---- body: render canvas + source pane, both fill the body ---- */}
      <div className="model3d__body">
        {/* Canvas stays mounted always; hidden (not unmounted) when the
            Source pane is active so the GL context survives the toggle. */}
        <div
          ref={mountRef}
          className="model3d__canvas"
          data-hidden={view === "render" ? "false" : "true"}
          style={{ cursor: status === "ready" ? "grab" : "default" }}
        >
          {status === "loading" && (
            <div className="model3d__skeleton">
              <Icon name="cube" size={56} strokeWidth={1.2} className="model3d__skeleton-cube" />
              <span className="model3d__skeleton-label">Loading 3D model…</span>
            </div>
          )}
          {status === "error" && (
            <div className="model3d__error">
              <div className="model3d__error-icon">
                <Icon name="alert" size={22} strokeWidth={1.5} />
              </div>
              <div className="model3d__error-text">{error || "Could not load model."}</div>
            </div>
          )}
          {status === "ready" && (
            <div className="model3d__hint" data-fade={interacted ? "true" : "false"}>
              drag · scroll · right-drag
            </div>
          )}
        </div>

        {view === "source" && (
          <div className="model3d__source">
            {hasText ? (
              <CodeView
                text={source.text}
                filename={fileName}
                mime={source.mime || sourceMime(format)}
              />
            ) : (
              <div className="model3d__sourcenote">
                <Icon name="cube" size={40} strokeWidth={1.2} />
                <div>{canShowSource ? source.note : "Loading source…"}</div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
