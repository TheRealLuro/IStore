# neuthek — what to build on the new machine (5070 / 12 GB)

This is the working roadmap for when the backend moves to the new box and Claude
is connected to it. Read this first, then `NGROK_SETUP.md`.

## 0. Onboarding the new machine (desktop = the 5070 box)

### How Claude works on the desktop while you chat from the laptop
The laptop stays your chat surface; **Claude Code runs ON the desktop** with
native access to the 5070, Docker, and the files. Pick one:

- **Remote Control — RECOMMENDED, zero networking setup.** On the desktop, in
  the repo, run `claude remote-control` (or `claude --rc`; or `/remote-control`
  inside a session). Then drive that *same* session from the laptop at
  **`claude.ai/code`** (browser) or the **Claude mobile app**. The agent executes
  on the desktop; you type from the laptop. It relays through Anthropic over
  **outbound HTTPS only** — NO SSH, NO port-forwarding, NO ngrok, no firewall
  changes on the desktop.
  - Requires: both ends signed into the **same Anthropic account**, on
    **Pro / Max / Team / Enterprise** (API-key auth is **NOT** supported).
  - Caveats: the desktop `claude` process must stay running; a >10-min network
    drop times out the *remote* link (local session unaffected); `/mcp`,
    `/plugin`, `/resume` are local-only (run those in the desktop terminal).
- **SSH — fallback / full control.** From the laptop, SSH into the desktop and
  run `claude` in that terminal. Everything native. Use **Tailscale** (free mesh
  VPN, no router config) for a stable, secure path; run under `tmux` so it
  survives disconnects.

> **ngrok is NOT for Claude's dev loop.** Remote Control already gives Claude the
> desktop. ngrok/tunnels are only for exposing the *running app's API* to
> EXTERNAL clients (Project A desktop apps, Project C connect-from-anywhere, or
> sharing a link). See `NGROK_SETUP.md` for that.

### Bring-up steps on the desktop
1. Install **Docker Desktop (WSL2 backend) + the NVIDIA Container Toolkit** so
   containers see the 5070. Verify with a recent CUDA base image:
   `docker run --rm --gpus all nvidia/cuda:12.8.0-base-ubuntu22.04 nvidia-smi`
   shows the 5070. (Blackwell/5070 needs **driver 570+ and CUDA 12.8+** for
   actual model inference — not just the nvidia-smi smoke test.)
2. Get the repo onto the desktop (git clone your remote, OR copy the `IStore`
   folder **including the current uncommitted changes**). Run `claude` from
   inside **WSL2** for parity with the laptop's bash + `docker exec` workflow.
3. `docker compose -p neuthek up -d`; `curl localhost:8000/health` → 200.
4. Set the 12 GB env flips (`NGROK_SETUP.md` §3): `TRANSLATE_MADLAD_4BIT=0`, etc.
5. A fresh Claude session has **none of the laptop's chat history** — the **repo
   is the source of truth**. This file + `CLAUDE.md` orient the new session; skim
   both first. (Auto-memory under `~/.claude` is machine-local and does NOT
   transfer.)

---

## PROJECT A — Native desktop apps (the big one)
Desktop clients that connect to the API and behave **exactly like the web app**, shipped as installable binaries. Brand-accurate (neuthek colors + fonts), responsive to the user's display resolution, clean minimized state, full feature parity with the web/mobile app.

- **Windows + Linux → C++.** One cross-platform C++ codebase (e.g. Qt or a webview-based shell — decide on the new machine after evaluating parity needs). Must look/function identically to the web app on both OSes.
- **macOS → Swift.** Native Swift app, same look & feel as the web version.
- All three are **thin clients over the existing REST API** — reuse the web app's flows; the heavy lifting stays server-side.
- **Settings → connection string / API link:** users can add or change the API base URL. Default = the neuthek-hosted server; self-hosters paste their own device's API URL here.
- **Minimized windows** render the app cleanly (not a blank/broken frame).
- **Correct resolution** per device; full app usable like the mobile layout.

### Decision notes (resolve on the new machine)
- C++ shell choice: Qt WebEngine (fast parity — wraps the existing SPA) vs. a fully native C++ UI (more work, truer "native"). The webview shell gets parity fastest and matches "look/function just like the web version."
- Swift: SwiftUI + WKWebView shell for parity, or fully native SwiftUI screens.

---

## PROJECT B — Self-hosted installer/setup `.exe` (C++)
A beautiful, branded Windows installer that sets up the whole self-hosted stack for non-technical users:
1. **Detect hardware** (CPU, RAM, GPU/VRAM, disks).
2. Let the user **select which storage device(s)** to allocate for neuthek.
3. **Auto-configure + start everything** (Docker stack, DB, MinIO, models sized to the detected GPU) with minimal/zero coding.
4. Brand-accurate, clean UX.
- Later: a **server/cluster setup** variant for connecting multiple nodes.

---

## PROJECT C — Connection-string + 2FA "connect to my server" flow
So a user on any client (desktop OR web) can point at a self-hosted device and connect securely:
1. User enters a self-hosted **API link / connection string** in Settings.
2. Backend sends a **verification code** (email, or the user's configured 2FA method).
3. User enters the code → the client is authorized to use that server.
4. Same account works across the neuthek-hosted server + their self-hosted device, on desktop and web.
- Backend: new "register a client/server link" endpoint + code issuance/verification, tied to the existing 2FA infra.

---

## Queued translation/AI fixes (now feasible at 12 GB)
These were blocked purely by 8 GB VRAM / model size on the old box:
1. **Tongan + low-resource quality** — swap the resident Qwen2.5-**1.5B** for a **7B/9B** instruct model (routing is already correct in `translate_engine.py`; it just needs a bigger model to be good). Also fixes "Tongan stuck on warming up" (currently 322 *sequential* 1.5B generations).
2. **Handwriting OCR** — add **TrOCR** (or evaluate the NVIDIA detector the user flagged) kept resident **alongside Florence**; 8 GB couldn't hold both, which is why image OCR timed out on handwriting.
3. **Translate images inside documents** — OCR each embedded figure → translate → render in place (needs detector + translator resident together).
   - **PROVEN on the old box (2026-06-03):** a model-free PyMuPDF inspection of the real docs (status report = 7× 1280×900 PNG marketing screenshots, all valid OCR-candidates; Kitaro pitch = 30+ figures) confirmed the enumerator **finds** the figures correctly — the figure that "stayed English" was an **OCR miss/timeout**, not an enumeration bug. On 8GB, Florence (single-orientation, busy card) can't reliably read a dense full-page screenshot inside the per-image 45s deadline, and image-heavy docs serially burned up to `_MAX_EMBEDDED_IMAGES × 45s ≈ 9 min` of tail latency (loader "stuck").
   - **Already shipped on the old box** to make it diagnosable + not hang: (a) observability logs in `translate_pdf._translate_embedded_images` (`found N figures but translated 0`, and `no raster figures qualified`); (b) an overall `_FIGURES_TOTAL_BUDGET_S = 75s` wall-clock budget for the whole figure stage so the doc returns promptly with whatever figures fit.
   - **On 12GB:** keep detector + translator resident together, use a stronger/ faster OCR (TrOCR or the NVIDIA detector), process figures **concurrently** (GPU headroom), and then RAISE/remove `_FIGURES_TOTAL_BUDGET_S` + `_MAX_EMBEDDED_IMAGES` since OCR is fast enough to do all figures.
4. **Phased image-translate loader** — restructure to `detect → check language → translate → replace`, with the loader's funny text reflecting the current phase (a set of variant lines per phase).

---

## Cleanup (do on the new machine, carefully)
- **Delete the old quick-setup scripts** and replace them with the Project B installer. (Deferred off the OLD box on purpose — deleting them there would break the currently-running stack the user is testing against. Do it fresh on the new machine while building the installer, after confirming which scripts are obsolete.)

---

## Branding reference
- Colors/fonts: the web app's CSS design tokens + `marketing/public/film.html` (the brand glyph / `NeuthekLoader` mark). Match these in every client.
