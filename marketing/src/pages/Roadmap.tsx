/* The lists below are pulled straight from the internal development
   tracker (todo.md) and the weekly /updates draft. Everything below
   describes the internal build. The marketing site is public; the
   product itself isn't released yet — neither the hosted version nor
   the open-source self-host build. "Shipped" means it's working in
   our development environment with tests; "Active work" means we're
   building it right now; "Designed" means it's spec'd but not
   started. Open-source self-host is a future commitment with no
   committed date — hosted launches first. */

const engineComplete = [
  // Sprint C — compliance & privacy (closed 2026-05-16 / 2026-05-17)
  { t: "Encryption at rest + in transit", d: "TLS termination via Caddy + auto-Let's-Encrypt + HSTS + HTTP/3. SSE-KMS dual-key on object storage (biometric vs. content). Age-encrypted Postgres backups via sidecar container. Fernet secret-box for refresh tokens + TOTP secrets. Boot-time validator fails fast on drift." },
  { t: "Access control + audit", d: "RBAC with user / admin / superuser roles, last-superuser demotion guard, signed-URL TTL clamp ≤ 5 min, brute-force lockout with exponential backoff, append-only audit trigger that raises on any UPDATE/DELETE. Postgres FORCE Row-Level Security on every per-user table (biometrics, geo, consents, share grants, recovery codes, bandit state)." },
  { t: "Hard account deletion", d: "Single source of truth in backend/deletion.py: originals + served + thumbnails + face crops in object storage, CLIP embeddings + summaries + EXIF rows + face detections + image_tags + share grants + cloud_files in the DB. Integration test seeds an image with every sibling row and asserts 0 rows / 0 objects after delete." },
  { t: "Compliance scaffolding", d: "PRIVACY.md (12 sections), DATA_PROCESSING.md DPA template, TERMS.md. Cookie banner rewritten to reflect we don't set cookies (localStorage only). Explicit age-13 gate at signup with FE block + backend validator. Consent log with timestamp + IP + user-agent + scope + signature for every grant/withdraw." },
  { t: "Repo hygiene", d: "Tightened .gitignore (secret-shaped files, AI weight caches, real-PII patterns). Three CI security jobs (gitleaks against full history, real-PII filename forbid, binary-fixture forbid under tests/). Pre-commit hook runs the same scans locally. Eight hygiene contracts asserted as pytest invariants inside the dev loop." },
  { t: "EXIF / GPS handling", d: "GPS + camera EXIF stripped at upload by default; opt-in toggle keeps location or camera data. Original blob untouched on strip — the format-specific re-encode drops the EXIF blob in place for JPEG / WebP / TIFF." },
  { t: "Consent before signup", d: "Register payload carries a consent bundle (ToS, privacy, AI features, biometric scopes). UserManager.create writes the ConsentRecord rows in the same transaction as the User row and audits each one. Legacy clients fall through to the post-signup consents modal." },
  { t: "Export + portability", d: "One-click account export ZIP carries every per-user row including CLIP embeddings + summaries + persons + faces + consents + audit log. Rate-limited to one full export per 24h via an audit row; returns 429 with Retry-After once exceeded." },
  { t: "Retention sweepers", d: "Five idempotent + audit-logged sweepers: expired originals, expired quarantine, feedback events (90d), audit-log anonymization (365d), scheduled account deletes (30-day grace). Audit anonymization works against the append-only trigger via a single permitted user_id → NULL transition." },

  // Product / UX surface
  { t: "Semantic search", d: "768-dim OpenCLIP ViT-L-14 embeddings stored in pgvector. Queries embed into the same space; cosine similarity ranks results. Cross-user query isolation enforced at the DB layer by RLS. Search fires as you type (280 ms debounce); WordNet + visual-domain synonym expansion (vibrant → colorful / vivid / bright / saturated)." },
  { t: "Hybrid CLIP + FTS search", d: "GET /search blends CLIP cosine with Postgres FTS over summary + summary_topic + summary_points + original_filename (0.45 / 0.55 weight, tsvector GIN index). Both \"sunset\" (semantic) and \"IMG_0420\" (exact filename) hit." },
  { t: "Inline preview surface", d: "Image lightbox with zoom + arrow-key navigation, multi-page PDF stack that lazy-loads each page, syntax-highlighted code preview for ~40 languages, reverse-geocoded GPS pin in-panel, tag / star / share from the same modal. Every preview is JWT-gated; Esc unwinds nested modals in the right order." },
  { t: "Best Of picker", d: "POST /images/best-of scores 2–30 photos on sharpness (OpenCV Laplacian) + exposure + face quality + optional use-case CLIP cosine. Three modes (overall / burst-cluster / use-case) and either 25 preset prompts (People / Scenes / Content / Style) or arbitrary free text. \"Keep this one\" moves the rest to Trash." },
  { t: "Trash + soft delete", d: "30-day recovery window for deleted items. Bulk Delete-forever path purges originals + every derived row in one transaction via the same backend/deletion.py path that powers account delete." },
  { t: "Content-aware compression", d: "LinUCB contextual bandit picks per-image codec + quality from a 32-dim feature vector (resolution / aspect ratio / detected screenshot vs. photo / colour count). WebP / MozJPEG / AVIF / JXL at q=55–92; lossless WebP for screenshots; animated GIF passthrough. Typically 40–70 % smaller than uniform JPEG-q85." },
  { t: "Upload validation hardening", d: "MIME + magic-byte sniffer + format-specific re-decode before storage. Polyglot trailer stripping. Forensic quarantine bucket on rejection. Archive uploads (zip / tar / optional 7z + RAR) walk path-traversal-safe + depth + expansion-ratio capped." },
  { t: "Folders, files, naming, organisation", d: "First-class folders with drag-drop + multi-select moves. AI-suggested smart filenames built from existing summaries. Type-pill ∧ folder filtering via a recursive CTE. Per-user tag system with 18 named chip colours, status-as-tag unification, and a folder-or-file picker popover." },
  { t: "Multi-select bulk actions", d: "Multi-select 2+ files in the gallery → Move to / new folder, Delete, Pick best of burst, tag, star. All bulk endpoints are scoped per-user, audit-logged, and respect the same RLS predicates as single-item endpoints." },
  { t: "Face clustering (opt-in)", d: "RetinaFace detection + ArcFace 512-dim embeddings + clustering into Me + tagged people. BIPA-grade: signed consent ledger, three-year auto-expiry of unrelated templates, immediate deletion on revoke. Manual image_persons link table guarantees the People count + drill-in always match." },
  { t: "Google Drive sync", d: "PKCE OAuth, drive.readonly scope only (we cannot write back), Fernet-encrypted refresh tokens, hourly background sweep, conflict banner when local edits diverge from remote, synthesised \"Google Drive\" folder tree per user. AI fenced out by default per Google Limited Use policy; per-source opt-in re-arms summary + face workers." },
  { t: "GitHub sync", d: "Own repos only (affiliation=owner), recursive tree walk, image-extension filter, secret-pattern skip list (.env*, id_rsa*, *.key, *.pem, credentials.json). Same OAuth + cloud_files schema as Drive." },
  { t: "Sign in with Google", d: "OAuth-based sign-in for new accounts and link-to-existing, with verified-email handling and the same consent bundle as password signup." },
  { t: "Two-factor auth + recovery codes", d: "TOTP-based 2FA via pyotp + QR endpoint; recovery codes generated on enrolment with constant-time verification at login. Backup-codes endpoint for re-issue." },
  { t: "Sharing primitive", d: "Per-image grants with email pinning, hashed share tokens, server-enforced 1-day cap for unverified recipients, full audit trail. Revoking a grant 404s the link immediately." },
  { t: "Stripe billing", d: "Free / Pro / Business tiers via Embedded Checkout, signature-verified webhooks, Customer Portal handoff for plan management + invoices. Past-due grace clock and promo-code UI are open follow-ups." },

  // Operator + scale (shipped 2026-05-17)
  { t: "Per-user fair queue + admin Queue tab", d: "ML job pipeline rewritten as per-user FIFO lists with round-robin worker pull. Per-user in-flight cap of 1 means the user just served goes to the back of the line; per-user queue cap of 1000 stops a script from filling Redis. Admin Queue tab polls every 3 s and exposes pending depth + in-flight + rate-limit headroom + a Drain button per user." },
  { t: "Heavy-endpoint rate limits", d: "Per-user hourly caps on every ML endpoint: backfill-summaries + backfill-vision 3 / hr, resummarize + redetect-faces 30 / hr, detect-and-label 10 / hr, best-of 30 / hr. Sized for legitimate everyday use but painful to script." },
  { t: "Admin Developer tab", d: "Live Capacity-estimate calculator: users / photos-per-user / avg MB / uploads-per-user-per-day → storage TB, RAM GB, VRAM GB, vCPU + ML worker count, predicted speeds for search / upload / CLIP embed / Florence caption. Constants measured from docker stats + nvidia-smi on the running stack, not back-of-envelope estimates." },
  { t: "Cross-vendor accelerator dispatch", d: "Heartbeat-driven probe + dispatch for CUDA, Intel XPU, and Apple MPS. Worker container detects available device at boot and routes inference through the matching torch backend with no operator config." },
  { t: "Email verification on waitlist", d: "HMAC-signed token, 7-day TTL, Resend HTTP-API integration with console fallback when no key. Per-IP + per-email rate-limited resend endpoint. \"Unverified\" rows are excluded from the launch ping." },
  { t: "Newsletter broadcast", d: "Admin can publish a weekly update to opted-in addresses. Per-recipient unsubscribe tokens, RFC 8058 List-Unsubscribe + One-Click-Post headers for Gmail compliance, dedup so re-sending the same slug is a no-op for already-delivered rows." },
  { t: "Marketing FAQ page", d: "22 Q&As with FAQPage JSON-LD and stable @id anchors so AI answer engines (ChatGPT, Perplexity, Google AI Overview, Bing Copilot) can deep-link to individual answers. Strengthened Organization / WebSite / SoftwareApplication JSON-LD + SearchAction on the homepage." },
  { t: "App brand mark", d: "NeuthekMark constellation glyph rendered in the sidebar + auth screen + favicon, matched to the marketing wordmark. Replaces the placeholder octahedron and Vite's default vite.svg." },
];

const activeWork = [
  { t: "Secret rotation worker", d: "Env hygiene + boot-time validator + gitleaks coverage shipped. Open: rotation worker for JWT_SECRET / MinIO creds / DB password + CLOUD_ENCRYPTION_KEY migration tool so rotating the secret-box doesn't orphan existing ciphertext. Rotation flow documented in SECURITY.md." },
  { t: "Multi-axis image filtering", d: "Today's gallery has one filter axis (type pill). Every signal needed (scene_label / indoor_outdoor / content_type / image_geo / face_detections / image_tags) is in the DB. Backend extends GET /images/ with composable query params; FE adds a filter strip with removable chips + URL persistence. Person + location chips gate on the matching consent scope." },
  { t: "Command-style search bar DSL", d: "Treat the search bar as a small parser: /find <query>, /show people: <name>, /best photo of <subject>, /in <folder>, /type <pill>, /before <date>, /after <date>. Falls back to natural-language semantic search if no command prefix matches." },
  { t: "Better image summaries", d: "Florence-2 detailed caption + scene-gated OCR + Qwen rewrite are wired. Open: a richer scene / object hint pass (RAM++ / Places365 / CogVLM2 tagger candidates) feeding structured hints into Qwen so summaries read \"auth-flow review on a whiteboard\" instead of \"person near a whiteboard\". Held-out eval set drives prompt tuning." },
  { t: "Better document summaries", d: "Qwen2.5-Instruct map-reduce over chunks + pypdf / pdfminer.six fallback wired. Open: per-chunk embeddings indexed alongside the doc so semantic search can jump to a section, and PyMuPDF rasterisation + Florence-2 OCR fallback for image-only scanned PDFs." },
  { t: "Profile / settings polish", d: "Display-name on signup (currently email everywhere). Me → display-name binding so AI summaries splice the real name instead of \"Me\". Staged-email banner during email-change re-verification. Per-category storage slider for original-retention." },
  { t: "Person re-detection on user signal", d: "User marks a photo as containing a person → backend re-runs RetinaFace at lower det_thresh + falls back to mediapipe face-mesh; if both empty, prompt the user to draw a box that becomes a labeled face." },
  { t: "Hardware tail — ROCm + OpenVINO NPU", d: "Detection + dispatch + heartbeat probe are in. Open: AMD ROCm wheels for Linux self-hosters and an OpenVINO inference path for Intel iGPU + NPU (requires converting Florence-2 / Qwen / CLIP to OpenVINO IR). Detection-only today." },
  { t: "Setup script for self-hosters", d: "Single-host scripts/setup.py: detect platform, probe CUDA / ROCm / MPS / Intel ARC, suggest the right torch wheel index, generate .env with fresh secrets, then docker-compose up -d or native install. CLI checklist first; Tk / browser wizard later." },
];

const designed = [
  { t: "Multi-data-type platform", d: "Promote images to one row in a wider assets table keyed by data_kind (image / video / document / contact / password / save / iot_event). Type-specific tables hang off assets.id. Search / encryption / sharing / retention all work across types from day one." },
  { t: "Password vault (end-to-end encrypted)", d: "Server stores ciphertext only. Encryption key derives from the user password via Argon2id + WebCrypto AES-GCM in the browser and never leaves the client. Recovery via the existing recovery-codes scaffold." },
  { t: "Contacts", d: "vCard / CSV import; per-contact fields (name, emails, phones, notes, photo). Searchable, foldered, taggable. Contact photos can opt in as a face source for the existing face_recognition scope." },
  { t: "Game saves + IoT data", d: "Game saves as versioned opaque blobs with per-game folder + last-N retention. IoT as time-series rows in a partitioned iot_events table per device-token, with a per-device timeline + simple chart and a hard per-device retention cap." },
  { t: "Comments on shared docs", d: "Per-asset comments with a free-form anchor (page+rect for PDFs, slide index for slideshows, time range for video). FE renders pins on the asset and a thread panel on the right. Layered on top of the existing sharing primitive." },
  { t: "Real-time team editing", d: "Document type only at first; out of scope for images. Likely path: y.js + a relay WebSocket, persisted snapshot per N seconds. Separate workstream; scheduled after the hardware tail lands so we know what budget we have on self-host." },
  { t: "Semantic search through folders", d: "Once content summaries stabilise, extend semantic search to match folder titles + aggregated child summaries so \"the trip to Mexico\" hits the folder, not just the photos. Cached aggregate embedding per folder; invalidated on child add / move / delete." },
  { t: "Fine-tune summaries from search behavior", d: "Log (query → clicked result) pairs (consented) and use them as a soft-label dataset to fine-tune the rewriter so future summaries match how the user phrases their searches. Per-user LoRA adapter so we never pollute a global model. Blocked on the model-training pipeline." },
  { t: "Model quantization", d: "Florence-2-large → 8-bit GPTQ for ≥ 8 GB GPUs, 4-bit for smaller; Qwen2.5-1.5B → 4-bit GGUF for CPU / Apple, GPTQ for CUDA / ROCm; CLIP + RetinaFace → ONNX INT8. Quant level becomes a config option, not a code change." },
  { t: "Lite no-AI profile", d: "Setup wizard offers a \"Lite\" path that disables Florence-2 + Qwen and falls back to BLIP captions + sumy summaries. Useful for Raspberry-Pi-class hosts and as a no-AI privacy stance for users who don't want any model inference on their library." },
  { t: "Account recovery email surface", d: "fastapi-users forgot-password + email-verify routes exist; needs the SMTP / Resend operator choice baked into .env.example, the verify-gate on sensitive endpoints, and the transactional-email templates with the brand mark." },
  { t: "B2B migration tooling", d: "Drag-a-folder-tree bulk import (SMB / NAS / mounted path with a service-account credential) or a desktop companion that streams uploads with resume support. Per-source consent scopes so legal can sign off per dataset. Migration dry-run (bytes + count + blocked items) before commit. Side-by-side phase keeps both systems live until DNS flips." },
  { t: "Docs + open-source readiness", d: "README rewrite, code-comment balance sweep, GitHub-ready .md polish, ruff + mypy --strict + tsc --noEmit gates in CI. Once this lands the self-host build is ready for a public source drop. No committed date for that drop — hosted launches first." },
];

export default function Roadmap() {
  return (
    <>
      <section className="page-head">
        <div className="container fade-in">
          <span className="eyebrow">Roadmap</span>
          <h1>What's working in dev, what's being built, what's on the horizon.</h1>
          <p className="lead">
            Every item below describes the internal development build.
            The product isn't released yet — neither the hosted version
            nor the open-source self-host build. <strong>Shipped</strong>{" "}
            means it's working in our development environment with
            tests; <strong>active work</strong> means we're building it
            now; <strong>designed</strong> means it's spec'd but not
            started. Hosted launches first; open-source self-host is
            planned with no committed date.
          </p>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <div className="roadmap">
            <div className="roadmap__col roadmap__col--shipped">
              <h3>Shipped (engine-complete)</h3>
              <ul className="roadmap__list">
                {engineComplete.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--building">
              <h3>Active work</h3>
              <ul className="roadmap__list">
                {activeWork.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
            <div className="roadmap__col roadmap__col--planned">
              <h3>Designed</h3>
              <ul className="roadmap__list">
                {designed.map((x) => (
                  <li key={x.t}><strong>{x.t}.</strong> {x.d}</li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2>Pre-launch checklist — status.</h2>
          <p className="lead" style={{ marginTop: 12 }}>
            We won't release publicly — open source or hosted — before
            every item below is closed. Most are in; the remaining ones
            are mostly operator-specific or a final external review.
          </p>
          <div className="cards">
            <div className="card">
              <h3>Security audit ✅</h3>
              <p>Auth + authz + JWT + upload validation + storage + secrets + rate limits + dependencies covered. See AUDIT.md and SECURITY_REVIEW.md.</p>
            </div>
            <div className="card">
              <h3>Privacy audit ✅</h3>
              <p>Every stored field has a documented reason, retention, and deletion path in PRIVACY.md §2. Account delete proven by an integration test that asserts 0 rows + 0 objects.</p>
            </div>
            <div className="card">
              <h3>Compliance checklist ✅</h3>
              <p>PRIVACY.md, TERMS.md, deletion, export, consent-before-signup, cookie banner accuracy, biometric opt-in, age-13 gate — all closed.</p>
            </div>
            <div className="card">
              <h3>AI/ML review ✅</h3>
              <p>No cross-user vector leakage (tested), no hidden biometric processing, no accidental training on user data. Models are pre-trained, frozen weights — we never fine-tune on your library.</p>
            </div>
            <div className="card">
              <h3>Deletion testing ✅</h3>
              <p>Every table + bucket + cache covered by tests/test_a5_full_deletion.py. Backup retention path documented in SECURITY.md "Encrypted backups → Retention + GDPR Article 17".</p>
            </div>
            <div className="card">
              <h3>Encrypted backups ✅</h3>
              <p>Age-encrypted pg_dump → local + offsite via a sidecar container. /admin/system surfaces the most recent successful backup and its age.</p>
            </div>
            <div className="card">
              <h3>Infra hardening 🟡</h3>
              <p>HTTPS via docker-compose.tls.yml + Caddy, encrypted backups, private buckets via SSE — all in. Operator-specific bits remain: firewall config, monitoring/alerting wiring, anti-malware integration beyond MIME + re-decode.</p>
            </div>
            <div className="card">
              <h3>Secret rotation ⏳</h3>
              <p>Env hygiene + gitleaks + boot validator shipped. Open: rotation worker for JWT_SECRET / MinIO / DB password + CLOUD_ENCRYPTION_KEY migration tool so rotating the secret-box doesn't orphan ciphertext.</p>
            </div>
            <div className="card">
              <h3>Threat model + external review ⏳</h3>
              <p>Security review pass shipped (AUDIT.md), but a formal STRIDE-style threat-model doc is owed, and an external review (another dev + a security person + a privacy person) is booked before public launch.</p>
            </div>
          </div>
        </div>
      </section>

      <section className="section section--ink">
        <div className="container">
          <h2>About open source.</h2>
          <p style={{ maxWidth: 720, marginTop: 12 }}>
            The self-host build will be released under an open-source
            license. The same engine powers both the self-host
            distribution and the managed hosted version, so there's no
            "open core" lockout. Self-host will be free and run via
            docker-compose; hosted exists for users who'd rather not
            run their own server.
          </p>
          <p style={{ maxWidth: 720, marginTop: 12 }}>
            <strong>No committed date for the public source drop yet.</strong>{" "}
            The codebase isn't fully cleaned up for public release —
            README rewrite, comment balance sweep, GitHub-ready .md
            polish, and CI lint tightening (ruff + mypy --strict +
            tsc --noEmit gates) are tracked in "designed" above.
            Hosted launches first; the source drop follows when the
            cleanup lands.
          </p>
        </div>
      </section>
    </>
  );
}
