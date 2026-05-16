// §A5 — FE cache eraser invoked after every server-side delete.
//
// Backend's `hard_delete_images` purges every row + blob; the FE
// mirrors that by:
//   1. Removing every React Query cache entry that could still
//      contain the deleted file. `invalidateQueries` marks stale;
//      `removeQueries` actually evicts the cached payload so a
//      stale-while-revalidate render can't briefly show the deleted
//      file.
//   2. Revoking any blob: URLs the auth-image hooks created for the
//      deleted ids. The hook itself revokes on unmount, but if a
//      component remains mounted (e.g., the preview panel still
//      showing the just-deleted file) we need to revoke here too.
//   3. Clearing the page's `caches` storage entries that match the
//      image's served / original / signed URLs. (No service worker
//      is registered today, so `caches` is empty in practice — the
//      call is still made for forward-compat with a future SW.)

const BLOB_URL_REGISTRY = new Map();

/** Register a blob URL so we can revoke it later when the underlying
 *  image is deleted. `auth-image.jsx` could call this to participate;
 *  the existing per-component cleanup keeps working when it doesn't. */
export function registerBlobUrl(imageId, blobUrl) {
  if (!imageId || !blobUrl) return;
  const set = BLOB_URL_REGISTRY.get(imageId) || new Set();
  set.add(blobUrl);
  BLOB_URL_REGISTRY.set(imageId, set);
}

function _revokeFor(imageId) {
  const set = BLOB_URL_REGISTRY.get(imageId);
  if (!set) return;
  for (const url of set) {
    try { URL.revokeObjectURL(url); } catch { /* ignore */ }
  }
  BLOB_URL_REGISTRY.delete(imageId);
}

async function _clearServiceWorkerCacheFor(imageIds) {
  if (typeof caches === "undefined") return;
  try {
    const names = await caches.keys();
    for (const name of names) {
      const c = await caches.open(name);
      const reqs = await c.keys();
      for (const req of reqs) {
        const url = req.url || "";
        if (imageIds.some((id) => id && url.includes(id))) {
          await c.delete(req);
        }
      }
    }
  } catch {
    // CacheStorage may be unavailable (private mode, etc.) — silent ok.
  }
}

/** Clear every FE cache + handle for one or more deleted image ids.
 *
 *  Pass a React Query client and the deleted id list. Safe to call
 *  with an empty/null id list — it just invalidates the list-level
 *  caches in that case (useful after bulk operations where the
 *  caller doesn't enumerate the affected ids). */
export async function eraseImageCaches(qc, imageIds = []) {
  const ids = (imageIds || []).filter(Boolean);
  // List-level keys that should re-fetch after any delete.
  const listKeys = [
    ["files"],
    ["storage"],
    ["geo"],
    ["facets"],
    ["account-trash"],
    ["incoming-shares"],
    ["image-shares"],
    ["people"],
    ["folders"],
    ["bestof"],
  ];
  for (const key of listKeys) {
    qc.invalidateQueries({ queryKey: key });
  }
  // Per-id evictions if anyone keyed by image id.
  for (const id of ids) {
    qc.removeQueries({ queryKey: ["image", id] });
    qc.removeQueries({ queryKey: ["image-shares", id] });
    qc.removeQueries({ queryKey: ["image-detail", id] });
    _revokeFor(id);
  }
  // Service-worker cache (no-op today, defensive for future SW).
  await _clearServiceWorkerCacheFor(ids);
}
