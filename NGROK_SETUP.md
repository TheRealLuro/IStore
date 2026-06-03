# Remote access (ngrok) + 12 GB GPU setup

Goal: run the neuthek backend on your **5070 (12 GB)** box, expose it with ngrok,
and hand Claude the link so we can work against the real, faster system.

---

## 1. Bring the stack up on the 5070

On the new machine, with Docker + the repo:

```bash
docker compose -p neuthek up -d
```

Wait for the backend to be healthy:

```bash
curl http://localhost:8000/health   # -> 200
```

---

## 2. Expose it with ngrok

```bash
# one-time: create a free account at ngrok.com, then:
ngrok config add-authtoken <YOUR_TOKEN>      # or paste it into ngrok.yml

# from the repo root:
ngrok start --all --config ngrok.yml
```

ngrok prints two HTTPS URLs:
- **web**  -> `https://xxxx.ngrok-free.app`  (the app — open in a browser)
- **api**  -> `https://yyyy.ngrok-free.app`  (the backend — Claude uses this)

**Give Claude BOTH URLs.** (If the free tier only lets one tunnel run, give the **api** one — that's all I need for diagnostics and to drive the translate/OCR/TTS endpoints.)

> Two small config items so the tunneled app talks to itself correctly — I'll
> help wire these once it's live, but in short: add the **web** URL to the
> backend's `CORS_EXTRA_ORIGINS`, and point the frontend's API base URL at the
> **api** URL (frontend `.env`). On a fresh tunnel the URLs change each run
> unless you reserve a domain — easiest is to just re-send me the new link.

---

## 3. Use the 12 GB headroom (env vars — no code change)

On the 5070, set these (compose `environment:` or `.env`) before bringing the
stack up. They were all forced into cramped 8 GB modes before:

```bash
# Translation: run MADLAD in 8-bit (better quality than the 4-bit we were forced into on 8 GB)
TRANSLATE_MADLAD_4BIT=0

# (After you send me the link I'll wire the heavier models the 8 GB card couldn't hold:)
#  - a 7B instruct LLM (Qwen2.5-7B / Gemma-2-9B) for *good* Tongan + other low-resource langs
#  - TrOCR (and/or the NVIDIA detector you mentioned) for real handwriting OCR
#  - keeping Florence + the detector resident together for fast in-image text detection
```

---

## 4. What I'll fix once it's on the 5070 + reachable

These were blocked purely by 8 GB VRAM / weak models, and become doable at 12 GB:

1. **Tongan (and low-resource langs) — real quality.** Swap the 1.5B LLM for a 7B/9B instruct model. The routing is already correct; it just needs a model big enough to be good.
2. **Handwriting OCR that actually works + fast.** Add TrOCR (or evaluate NVIDIA's detector you flagged) as a resident model alongside Florence — the 8 GB card couldn't hold both, which is why it kept timing out.
3. **Translate the images *inside* documents.** OCR each embedded figure, translate, render in place — needs the detector + translator resident together.
4. **Phased image-translate loader.** Restructure the pipeline to `detect → check language → translate → replace`, and make the loader's funny text reflect the current phase (a set of lines per phase).

Send the link and I'll start on these in order.
