# Repository Hygiene (§A7)

Reference document for operators + contributors covering:

- What gets `.gitignore`d and why
- How secrets are kept out of the repo (gitleaks + pre-commit)
- The synthetic-fixtures rule for tests
- The historical `node_modules/` tracking issue and how to clean it
- The "audit git history" checklist from todo.md §A7

## TL;DR

| Surface | Status | Where |
|---|---|---|
| `.gitignore` covers `.env`, `data/`, `*.dump`, model weights, etc. | ✅ | [.gitignore](.gitignore) |
| Gitleaks scans every PR + push to `main` with full history | ✅ | [.github/workflows/security.yml](.github/workflows/security.yml) |
| Pre-commit hook runs gitleaks locally before commit | ✅ | [.pre-commit-config.yaml](.pre-commit-config.yaml) |
| Real-PII filename heuristic CI step | ✅ | `forbid-real-pii-fixtures` job |
| Synthetic-fixtures invariant CI step | ✅ | `synthetic-fixtures` job |
| Test fixtures generated in-process (no binaries tracked) | ✅ | `tests/conftest.py`, `_png_bytes()`, `_docx_bytes()` |
| `node_modules/` historically tracked (~8.6k files) | ⚠️ known debt | see §"Untracking node_modules" |

---

## What's in `.gitignore` and why

[.gitignore](.gitignore) blocks the obvious categories. Highlights
relevant to A7:

- `.env`, `.env.local`, `.env.*.local` — anything secret-shaped lives
  here. `.env.example` is exempt (it documents the format).
- `data/`, `local_blobs/`, `backups/`, `exports/`, `uploads/` —
  runtime payloads.
- `*.dump`, `*.sql`, `*.sql.gz`, `*.bak`, `*.sqlite*`, `*.db` — local
  database dumps and SQLite caches.
- `*.age`, `*.gpg`, `*.enc`, `*.kdbx` — encrypted ciphertext files.
- `id_rsa`, `id_ed25519`, `*.pem`, `*.key`, `*.crt`, `*.p12`,
  `*.agekey` — private key blobs.
- `.netrc`, `.npmrc`, `.pypirc`, `.cargo/credentials`,
  `.docker/config.json` — files that often carry tokens.
- Real-PII-shaped filenames (`*passport*`, `*ssn*`, `*real_user*`,
  `*prod_*.dump`) — heuristic guard.
- `.cache/`, `.hf_cache/`, `.transformers_cache/`,
  `~/.cache/huggingface/`, `*.safetensors`, `*.onnx`, `*.gguf`,
  `*.bin` — model weight caches.

For paths so sensitive they shouldn't even be _named_ in a tracked
file, use `.git/info/exclude` (local-only, never published).

---

## Secret-scanning

### Gitleaks in CI

[.github/workflows/security.yml](.github/workflows/security.yml)
runs gitleaks against the full git history (`fetch-depth: 0`) on
every PR and push to `main`. Findings fail the build and upload an
artifact to the workflow run.

The config [`.gitleaks.toml`](.gitleaks.toml) allowlists:

- `.env.example` and `SECURITY_REVIEW.md` (placeholder values by
  design)
- Migration + test files (carry dummy Argon2 hashes used as
  constant-time-fail probes)
- The CI workflow itself (documents env-var names)
- The literal dummy Argon2 hash + the `dev-only-jwt-secret-CHANGE-IN-PROD`
  sentinel that the prod-boot validator rejects

Anything else flagged should be treated as a **real** finding.

### Gitleaks locally (pre-commit)

[.pre-commit-config.yaml](.pre-commit-config.yaml) runs the same
gitleaks scan against staged files before the commit lands. To
install:

```bash
pip install pre-commit
pre-commit install
```

Subsequent `git commit` invocations run the hooks and abort the
commit on a finding. Catches the leak _before_ it touches the
remote, vs. the CI pass which catches it _after_ the commit has
already entered the remote history.

The pre-commit chain also blocks:

- Real-PII-shaped filenames (passport, SSN, etc.) being staged.
- New paths under `node_modules/` being added to the index (the
  historical entries are documented below; new ones get refused).

---

## Synthetic fixtures rule

Tests in `tests/` must generate their input bytes in-process. We do
not check in real photos, real PDFs, real EXIF, or real embeddings.
The `synthetic-fixtures` CI job enforces this by searching `tests/`
for `.jpg/.jpeg/.png/.heic/.gif/.mp4/.mov/.pdf/.dump/.sql` files and
failing the build if any are found.

Patterns used inside the test suite:

- `_png_bytes()` — emits a 1×1 RGB PNG via `PIL.Image.new(...)`.
- `_docx_bytes(extra_files=())` — assembles a tiny OOXML zip in
  `io.BytesIO`.
- `insert_face(user_id, ...)` — inserts a `Face` row with a synthetic
  512-d embedding (`[0.001 * (i % 17) for i in range(512)]`).
- `_seed_image_with_full_data(user_id, ...)` — used by the §A5
  deletion test; seeds every sibling row with deterministic synthetic
  values.

No real PII is required; no real PII should ever appear.

---

## Untracking `node_modules/`

Historical state: ~8,688 files under `node_modules/` and
`frontend/node_modules/` are tracked in the index. This is **bloat,
not a security leak** — npm packages are public — but it inflates
clone size and trips the synthetic-fixtures heuristic on every dep
that ships sample data.

The `.gitignore` already lists `node_modules/` and
`frontend/node_modules/`, so new packages don't get added. The
historical tracked entries need a one-time cleanup:

```bash
# From the repo root:
git rm -r --cached node_modules frontend/node_modules marketing/node_modules || true
git commit -m "chore: untrack node_modules per A7 hygiene"
# Push the cleanup commit. Subsequent checkouts will look clean.
```

After this commit, devs who clone the repo will not get
`node_modules/` from git — they run `npm install` to populate it
locally. The `.gitignore` keeps them ignored thereafter.

This cleanup is intentionally left to an operator decision because:

1. The cleanup commit is large (~8.6k file deletions).
2. CI / Docker builds that don't run `npm install` would break until
   adjusted.
3. The history will still contain the bytes — `git filter-repo` is
   needed if the goal is to fully purge them. That's a force-push
   to history and breaks any open PRs / forks.

If you choose to run filter-repo, the steps are:

```bash
git filter-repo --invert-paths --path node_modules --path frontend/node_modules
# DESTRUCTIVE — coordinate with the team first.
git push --force-with-lease origin main
```

---

## "Audit git history" checklist (todo.md §A7)

Running this checklist against the current `main`:

| What to look for | How | Status |
|---|---|---|
| Real user images | `git ls-files \| grep -iE '\.(jpe?g\|png\|heic\|raw)$'` | Only node_modules vendored test data — no real photos |
| Real EXIF | Implicit via images above | None |
| Real embeddings | `git log --all -S 'np.array(' -- '*.npy'` | None |
| Prod credentials | `gitleaks detect --source .` against full history | Clean (no findings) |
| DB dumps | `git ls-files \| grep -iE '\.(dump\|sql\|sql\.gz\|bak\|sqlite)$'` | None |
| API tokens / keys | `gitleaks detect --redact` | Clean |

If a future audit surfaces sensitive bytes, the recovery procedure
is:

1. `git filter-repo --replace-text replacements.txt` (or
   `--invert-paths --path <bad-file>`).
2. Force-push with team coordination.
3. **Rotate every secret** that was in the leaked bytes — assume
   they're public from the moment the original commit landed.

---

## Operator checklist

When standing up a new neuthek deployment, verify:

- ☐ `pre-commit install` ran inside the dev clone.
- ☐ The `security.yml` workflow ran green at least once.
- ☐ No `.env` / `.env.local` is in the index (`git ls-files | grep -E '^\.env'`
  returns only `.env.example`).
- ☐ `node_modules/` untracked or cleanup plan in place.
- ☐ All operator-side secrets (Stripe, age recipient, JWT, etc.)
  live in the secret manager, not in env files that risk being
  staged.
