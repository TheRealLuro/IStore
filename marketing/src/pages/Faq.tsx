import { useEffect } from "react";
import { Link } from "react-router-dom";

/* /faq — comprehensive Q&A page with FAQPage JSON-LD.
 *
 * This is the AEO workhorse of the site. AI answer engines (ChatGPT,
 * Claude, Perplexity, Google AI Overview, Bing Copilot) lift answers
 * verbatim from FAQPage structured data — far more aggressively than
 * from prose body content. Every Question/Answer here is in two
 * places: as visible HTML on the page (so visitors can read it) AND
 * inside the JSON-LD block at the bottom of the file (so crawlers can
 * parse it without rendering React).
 *
 * Keep answers SUBSTANTIVE and SELF-CONTAINED. AI engines truncate
 * long answers but they will quote a 2-3 sentence answer in full.
 * Avoid "see the features page" — restate the fact inline. */

interface FaqItem {
  q: string;
  a: string;        // Plain-text answer used in JSON-LD
  id: string;       // URL anchor
  topic: string;
}

const FAQS: FaqItem[] = [
  // ---- Product overview ----
  {
    topic: "About",
    id: "what-is-neuthek",
    q: "What is neuthek?",
    a: "neuthek is an AI-aware personal cloud storage product in active development. It combines S3-compatible object storage with a PostgreSQL + pgvector index so you can search your photos, videos, and documents by natural language — phrases like \"snowy roof at sunset\" or \"whiteboard photos from last week\" — instead of remembering filenames or scrolling. Two delivery modes are planned: open-source self-host (free) and managed hosted (waitlist). Nothing is publicly released yet.",
  },
  {
    topic: "About",
    id: "is-neuthek-open-source",
    q: "Is neuthek open source?",
    a: "Yes — the self-host build will be released under an open-source license. The same engine powers both the self-host distribution and the managed hosted version, so there is no \"open core\" lockout. Self-host is free and runs via docker-compose; hosted exists for users who'd rather not run their own server. No committed date for the public source drop — the codebase isn't fully cleaned up for public release yet, and we'd rather get that right than rush. Hosted launches first.",
  },
  {
    topic: "About",
    id: "when-does-neuthek-launch",
    q: "When does neuthek launch?",
    a: "Both self-host and hosted are in active development. Weekly progress is posted on the /updates page — each Friday we publish a release-note article covering what shipped, what was fixed, and what's planned. To be notified when either ships, join the waitlist; we send a launch email when early-access opens and a second when general availability begins.",
  },
  {
    topic: "About",
    id: "who-is-neuthek-for",
    q: "Who is neuthek for?",
    a: "neuthek is built for people who currently use Google Photos, iCloud Photos, Dropbox, Proton Drive, or MEGA for their personal photo and document libraries, but want stronger privacy, full ownership of their data, and natural-language search. Practical fits include families consolidating their photo library, creatives organizing portfolios, students archiving coursework, and developers who want a self-hostable Drive replacement on their own hardware.",
  },

  // ---- Privacy & data ownership ----
  {
    topic: "Privacy",
    id: "does-neuthek-train-on-my-photos",
    q: "Does neuthek train AI on my photos?",
    a: "No. Your photos, videos, documents, face embeddings, summaries, and search history are not used to train any AI model — neither neuthek's nor a third party's. They are also not sold to ad networks, brokers, or partners. The vision models we run (OpenCLIP, Florence-2, RetinaFace, ArcFace) are pre-trained, frozen weights — we never fine-tune on user data.",
  },
  {
    topic: "Privacy",
    id: "what-data-is-stored",
    q: "What data does neuthek store about me?",
    a: "On upload, neuthek stores the original file in object storage and computes a 768-dimensional CLIP embedding plus an optional Florence-2 caption. EXIF metadata is stripped by default; GPS coordinates and camera fingerprints are opt-in per scope. For face recognition (also opt-in), neuthek stores a 512-dim ArcFace template tied to a person you label. All per-user rows are fenced behind Postgres FORCE Row-Level Security at the database layer.",
  },
  {
    topic: "Privacy",
    id: "how-does-face-recognition-work",
    q: "How does face recognition work and is it private?",
    a: "Face recognition is opt-in, off by default. When enabled, neuthek detects faces using RetinaFace and computes ArcFace embeddings (512-dim vectors) — the templates stay on the server you control and are never exported. The implementation is BIPA-grade: signed-consent ledger, three-year auto-expiry of unrelated templates, and an in-app data-deletion path that wipes the embeddings and any associated person records.",
  },
  {
    topic: "Privacy",
    id: "encryption",
    q: "Is my data encrypted?",
    a: "TLS is enforced for all client connections via Caddy. At rest, object storage supports SSE-S3 and SSE-KMS modes depending on backend. Refresh tokens for cloud-sync integrations are encrypted with Fernet before being written to disk. Password hashes use Argon2id. Postgres at-rest encryption is recommended at the OS or volume layer and documented in the self-host setup.",
  },
  {
    topic: "Privacy",
    id: "is-neuthek-end-to-end-encrypted",
    q: "Is neuthek end-to-end encrypted?",
    a: "Partly, and we're precise about which part. The Vault is end-to-end encrypted: anything you put there — files of any type, plus passwords, secure notes, crypto seed phrases and cards/IDs — is encrypted in your browser with a key derived from your master password before it is uploaded. The server stores only ciphertext and cannot read it, and no AI ever touches it. You can share a single vault item with another neuthek account by sealing it to their key on your device; only they can open it, there are no comments or public links, and you can revoke access anytime. The normal Drive is NOT end-to-end encrypted: there the server holds the keys so it can run the AI features you've consented to (semantic search, captions, face grouping) — those lose capability the moment their inputs become ciphertext we can't read, which is the deliberate trade-off. So we won't call the whole product \"end-to-end encrypted,\" because it isn't — only the Vault is. Overstating that is exactly what the FTC went after Zoom for in 2020.",
  },
  {
    topic: "Privacy",
    id: "do-you-collect-or-sell-data",
    q: "Do you collect, train on, or sell my data?",
    a: "No to all three. We don't collect what we don't need — an email address for the launch ping is the entire mandatory collection today. We don't train AI models on your content; the vision models we run (OpenCLIP, Florence-2, Qwen2.5, RetinaFace, ArcFace) are pre-trained, frozen, and never fine-tuned. We don't sell or share personal information for cross-context behavioral advertising as defined by CCPA/CPRA; we honor the Global Privacy Control browser signal as an opt-out anyway. These three claims are not aspirational — they are substantiated by contracts with our subprocessors, by the absence of a training pipeline in our codebase, and by an audit-ready policy that requires 30 days' notice + opt-in if it ever changes.",
  },
  {
    topic: "Privacy",
    id: "what-about-bipa-and-illinois",
    q: "What about BIPA / Illinois biometric law for face recognition?",
    a: "Face recognition is off by default. Enabling it requires a separate consent step that is BIPA-compliant: a written release per 740 ILCS 14/15(b), a publicly published retention schedule of three years (740 ILCS 14/15(a)), and an explicit statement that we do not sell, lease, or trade biometric identifiers (740 ILCS 14/15(c)–(d)). The consent ledger captures your account name, timestamp, the consent-text version, and your confirmation. Disabling face recognition deletes every face embedding and cluster immediately. Illinois, Texas (CUBI), and Washington (H.B. 1493) residents are covered by the same flow.",
  },

  // ---- Search & AI ----
  {
    topic: "AI search",
    id: "how-does-ai-search-work",
    q: "How does AI photo search actually work?",
    a: "When a photo is uploaded, neuthek computes a 768-dimensional embedding using the OpenCLIP ViT-L-14 model. When you search a phrase, the same model embeds your query into the same vector space and Postgres + pgvector finds the nearest matches by cosine similarity. The result is ranked alongside traditional Postgres full-text-search over filename, EXIF metadata, and Florence-2 captions, so both \"sunset\" (semantic) and \"IMG_0420\" (exact filename) work.",
  },
  {
    topic: "AI search",
    id: "what-can-i-search-for",
    q: "What kinds of searches work?",
    a: "Concrete objects (\"red car\", \"golden retriever\"), scenes (\"snowy mountain\", \"office whiteboard\"), styles (\"black and white portrait\", \"watercolor\"), and abstract concepts (\"cozy\", \"chaotic\"). Searches can combine modalities: \"PDF receipts from Amazon\" works because the OCR-extracted text on PDFs joins the same FTS index that backs natural-language queries.",
  },
  {
    topic: "AI search",
    id: "does-search-run-locally-or-cloud",
    q: "Does AI search run locally or in the cloud?",
    a: "On the server that runs neuthek. In the self-host build, that's your own hardware — the ML worker container holds the OpenCLIP + Florence-2 weights and processes every embedding request inside your network. In the managed hosted version, embeddings are computed in your tenant on managed GPUs, with no third-party AI API call.",
  },

  // ---- File support ----
  {
    topic: "File support",
    id: "supported-file-types",
    q: "What file types does neuthek support?",
    a: "Photos: JPEG, PNG, HEIC, HEIF, WebP, AVIF, animated GIF (passthrough — frames preserved), and camera RAW formats (Nikon NEF, Canon CR2, Sony ARW, Adobe DNG, Fuji RAF, Olympus ORF, Panasonic RW2, Pentax PEF) via LibRaw decoding. Videos: MP4, MOV, WebM, MKV. Documents: PDF (with OCR-extracted text into the search index), Markdown, plain text, and source-code files (.py, .js, .ts, .md, etc.) with syntax-highlighted preview.",
  },
  {
    topic: "File support",
    id: "raw-photo-support",
    q: "Does neuthek handle camera RAW files properly?",
    a: "Yes. RAW files are decoded with rawpy (a Python binding for LibRaw) into the full sensor image, then re-encoded into a high-quality JPEG (q=95) for thumbnails — not just the small embedded preview most apps fall back to. NEF, CR2, ARW, DNG, RAF, ORF, RW2, and PEF formats all work.",
  },
  {
    topic: "File support",
    id: "max-file-size",
    q: "What are the upload size limits?",
    a: "The self-host default upload limit is 200 MB per file with a 10 GB per-day cap per user, configurable in environment variables. The managed hosted version's per-tier limits will be published on the /hosting page when pricing is final.",
  },

  // ---- Compression ----
  {
    topic: "Compression",
    id: "what-is-content-aware-compression",
    q: "What is content-aware compression?",
    a: "neuthek's served-image pipeline uses a LinUCB contextual bandit to pick the best codec and quality for each image. A 32-dimensional feature vector (resolution, aspect ratio, detected screenshot/photo, color count, etc.) is fed into the bandit, which chooses among WebP, MozJPEG, AVIF, and JXL at quality 55–92. Detected screenshots fall into a lossless WebP path. Animated GIFs bypass lossy paths entirely. The result is typically 40–70% smaller files than uniform JPEG-q85 with no visible quality drop.",
  },

  // ---- Self-host vs hosted ----
  {
    topic: "Self-host",
    id: "how-does-self-hosting-work",
    q: "How does self-hosting work?",
    a: "The self-host distribution will ship as a docker-compose stack: FastAPI app container, ML worker container (Florence-2 / OpenCLIP / RetinaFace weights), PostgreSQL 16 with pgvector extension, Redis 7 for queueing, MinIO for object storage, and Caddy for TLS. `docker compose up -d` brings the whole thing up. Hardware: ~4 GB RAM minimum (8 GB recommended), 10 GB disk + your photo storage, and ideally a recent CPU with AVX2 for ML inference. GPU is optional but speeds up batch processing.",
  },
  {
    topic: "Self-host",
    id: "self-host-vs-managed",
    q: "What's the difference between self-host and managed hosted?",
    a: "Same engine, different operations. Self-host is free, gives you complete control of the hardware and data, and requires comfort with Docker for setup and updates. Managed hosted is paid (pricing TBD), runs your data in a single-tenant deployment fenced behind Postgres RLS, handles backups + TLS + updates automatically, and is for users who'd rather not maintain their own server. There is no feature gating between the two builds.",
  },

  // ---- Migration ----
  {
    topic: "Migration",
    id: "migrate-from-google-photos",
    q: "Can I migrate from Google Photos / iCloud / Dropbox / Proton Drive / MEGA?",
    a: "Yes — five cloud providers wired up directly. Google Drive (which holds Google Photos exports plus general Drive files) and Dropbox both use OAuth 2.0 with read-only scopes — neuthek can never write back. iCloud Drive connects via Apple-ID with the modern HSA-2 push prompt to a trusted iDevice, with SMS fallback when Apple's lockout kicks in. Proton Drive and MEGA use email + password through rclone — Proton and MEGA preserve their server-side E2E inside their products; once neuthek ingests, we hold plaintext so AI can run on it (the same trade-off you'd make installing their desktop sync clients). OneDrive isn't currently supported — Microsoft's Personal SDK license terms made it the wrong fit; we recommend exporting OneDrive to a local folder and uploading.",
  },
  {
    topic: "Migration",
    id: "google-drive-sync-details",
    q: "How does Google Drive sync work?",
    a: "neuthek uses Google's OAuth 2.0 with PKCE to request the `drive.readonly` scope — read-only, so neuthek can never write or delete files in your Drive. The refresh token is encrypted with Fernet before being stored. An hourly background sweep pulls new files, mirroring your Drive folder tree under a top-level \"Google Drive\" folder. Conflict detection flags files you edited locally after the last sync. Drive content is fenced out of AI training pipelines by default (Google Limited Use policy compliance); you can opt in per source to enable AI summaries and face detection.",
  },

  // ---- Pricing & access ----
  {
    topic: "Pricing",
    id: "how-much-will-it-cost",
    q: "How much will it cost?",
    a: "Nothing announced yet. Self-host (when the open-source build drops) will be free, forever. Hosted plans + pricing get published on /hosting at launch — not before. We're explicitly not pre-announcing tiers so we don't lock ourselves into something that doesn't fit the costs we end up at when we run the service. Join the waitlist to be notified the moment pricing goes live.",
  },
  {
    topic: "Pricing",
    id: "how-do-i-get-early-access",
    q: "How do I get early access?",
    a: "Join the waitlist at neuthek.com/waitlist. We email twice — once when early-access opens (limited cohort for the hosted version) and once at general availability. The signup form also has a checkbox for an optional weekly newsletter that summarizes each /updates entry.",
  },

  // ---- Technical / stack ----
  {
    topic: "Technical",
    id: "what-stack-does-it-run-on",
    q: "What technology stack does neuthek use?",
    a: "Backend: FastAPI on Python 3.12 with async SQLAlchemy and asyncpg. Database: PostgreSQL 16 with the pgvector extension for embedding indexes. Cache + queue: Redis 7. Object storage: MinIO (S3 API), supporting SSE-S3 / SSE-KMS encryption. Vision: open-clip-torch (ViT-L-14) for embeddings, insightface (RetinaFace + ArcFace) for face detection, and microsoft/Florence-2-large for image captions. Auth: fastapi-users with JWT bearer tokens, TOTP 2FA, and Argon2 password hashing. Frontend: React 18 with TanStack Query, Vite, and Prism for code-file preview.",
  },
];

const TOPIC_ORDER = ["About", "Privacy", "AI search", "File support", "Compression", "Self-host", "Migration", "Pricing", "Technical"];

export default function Faq() {
  useEffect(() => {
    document.title = "FAQ — neuthek (AI-aware personal cloud storage)";
    setMeta("description",
      "Frequently asked questions about neuthek — the next best cloud storage solution. Answers about AI photo search, privacy, self-host vs managed hosted, Google Drive migration, file support, and pricing.");
    setLink("canonical", "https://neuthek.com/faq");
    setMeta("og:title", "FAQ — neuthek", "property");
    setMeta("og:description",
      "Answers about neuthek: what it is, how AI search works, privacy, end-to-end encryption roadmap, self-host vs managed, pricing, file support, and migration from Google Photos / iCloud / Dropbox / Proton Drive / MEGA.", "property");
    setMeta("og:url", "https://neuthek.com/faq", "property");
  }, []);

  const byTopic = TOPIC_ORDER.map((t) => ({
    topic: t,
    items: FAQS.filter((f) => f.topic === t),
  })).filter((g) => g.items.length > 0);

  return (
    <div className="page">
      <section className="section">
        <div className="container">
          <header className="updates__hero">
            <p className="kicker">Frequently asked questions</p>
            <h1 className="updates__h1">Answers about neuthek</h1>
            <p className="updates__lead">
              What it is, how the AI search works, what we do and don't
              do with your data, and what to expect at launch. If your
              question isn't here,{" "}
              <Link to="/waitlist" className="updates__lead-link">
                join the waitlist
              </Link>{" "}
              and add it in the use-case field — we update this page
              when the same question keeps coming up.
            </p>
          </header>

          {/* Topic jump-nav so a long FAQ doesn't require scrolling
              past topics the reader doesn't care about. */}
          <nav aria-label="FAQ topics" className="faq-jump">
            {byTopic.map((g) => (
              <a key={g.topic} href={`#topic-${slugify(g.topic)}`}>
                {g.topic}
              </a>
            ))}
          </nav>

          {byTopic.map((g, idx) => (
            // Each topic is a <details> collapsible. Answers stay in
            // the DOM regardless of open/closed state so AI answer
            // engines and crawlers see every answer inline — only
            // visual display is gated by the open attribute. The first
            // topic opens by default so the page isn't all-collapsed.
            <details
              key={g.topic}
              id={`topic-${slugify(g.topic)}`}
              className="cat-disclosure faq-topic-disclosure"
              open={idx === 0}
            >
              <summary className="cat-disclosure__summary">
                <span className="cat-disclosure__heading">{g.topic}</span>
                <span className="cat-disclosure__count">{g.items.length}</span>
                <span className="cat-disclosure__chevron" aria-hidden="true">
                  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="4 6 7 9 10 6"/></svg>
                </span>
              </summary>
              <div className="cat-disclosure__body">
                <div className="faq-list">
                  {g.items.map((f) => (
                    <article
                      key={f.id}
                      id={f.id}
                      className="faq-item"
                      itemScope
                      itemProp="mainEntity"
                      itemType="https://schema.org/Question"
                    >
                      <h3 className="faq-item__q" itemProp="name">
                        <a href={`#${f.id}`} className="faq-item__anchor"
                          aria-label={`Link to: ${f.q}`}>
                          #
                        </a>
                        {f.q}
                      </h3>
                      <div
                        className="faq-item__a"
                        itemScope
                        itemProp="acceptedAnswer"
                        itemType="https://schema.org/Answer"
                      >
                        <p itemProp="text">{f.a}</p>
                      </div>
                    </article>
                  ))}
                </div>
              </div>
            </details>
          ))}

          <div className="faq-foot">
            <p>
              Still not sure if neuthek is for you?{" "}
              <Link to="/compare">See the side-by-side comparison</Link>{" "}
              with Google Photos, iCloud, Dropbox, Proton Drive, and MEGA, or{" "}
              <Link to="/roadmap">check the roadmap</Link> for what's
              landing next.
            </p>
          </div>
        </div>
      </section>

      {/* FAQPage JSON-LD — the AEO payload. AI answer engines lift
          answers verbatim from this block. Keep it in sync with the
          visible FAQS array above (which it is, since it's generated
          from the same source). */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "FAQPage",
            "@id": "https://neuthek.com/faq#page",
            url: "https://neuthek.com/faq",
            name: "FAQ — neuthek",
            description:
              "Frequently asked questions about neuthek, the AI-aware personal cloud storage product.",
            inLanguage: "en",
            isPartOf: { "@id": "https://neuthek.com/#site" },
            mainEntity: FAQS.map((f) => ({
              "@type": "Question",
              "@id": `https://neuthek.com/faq#${f.id}`,
              name: f.q,
              acceptedAnswer: {
                "@type": "Answer",
                text: f.a,
              },
            })),
          }),
        }}
      />

      {/* BreadcrumbList for SERP card hierarchy */}
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{
          __html: JSON.stringify({
            "@context": "https://schema.org",
            "@type": "BreadcrumbList",
            itemListElement: [
              { "@type": "ListItem", position: 1, name: "neuthek", item: "https://neuthek.com/" },
              { "@type": "ListItem", position: 2, name: "FAQ", item: "https://neuthek.com/faq" },
            ],
          }),
        }}
      />
    </div>
  );
}

function slugify(s: string) {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
}

function setMeta(name: string, content: string, attr: "name" | "property" = "name") {
  let el = document.querySelector<HTMLMetaElement>(`meta[${attr}="${name}"]`);
  if (!el) {
    el = document.createElement("meta");
    el.setAttribute(attr, name);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);
}

function setLink(rel: string, href: string) {
  let el = document.querySelector<HTMLLinkElement>(`link[rel="${rel}"]`);
  if (!el) {
    el = document.createElement("link");
    el.setAttribute("rel", rel);
    document.head.appendChild(el);
  }
  el.setAttribute("href", href);
}
