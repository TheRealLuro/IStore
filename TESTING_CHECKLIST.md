# neuthek — Full Test Checklist

Everything completed this session, grouped by area. App runs at **http://localhost:5173** (logged in as jasonk).
Check each box as you confirm it. The full queue is done — every section below is ready to test.

> **Status:** Everything below is wired, built, and ready to test.
> - **AI summaries (#184):** every file type now produces a useful, searchable summary. PowerPoint `.pptx` had no text extractor — I added one (slide titles + body + speaker notes); confirmed working (sample.pptx → *"Neuthek presentation… bullet points for three topics… concluding with thanks"*). A document re-summarize pass is finishing in the worker (a couple minutes).
> - **Person re-detection (#98):** wired + verified (backend healthy, route registered, FE built). Test steps are section 15.

---

## 1. Loading animation (#168)
- [ ] Hard-refresh the app (Ctrl+Shift+R). The boot loader shows the **neuthek logo (nodes) inside a circle**.
- [ ] Nodes and loader are **large** (not tiny).
- [ ] There's a **solid inner ring** with a **spinning dotted outer ring** around it.
- [ ] **Crafty typewriter text** cycles underneath (Claude-Code style), switching ~every 2s with a blinking caret.
- [ ] No layout jump / flash when the app finishes loading.

## 2. Navigation & breadcrumbs (#176)
- [ ] Enter a folder, then a subfolder — breadcrumb shows the **true path**.
- [ ] **Repro the old bug:** go into a folder → click a sidebar menu item (e.g. Photos/Recents) → click a *different* folder. The breadcrumb should reset correctly, NOT show a fake nested path (folder-within-folder that isn't real).
- [ ] Clicking any breadcrumb crumb jumps to exactly that level.

## 3. Command palette & search (#99, #100)
- [ ] Press **⌘K / Ctrl+K** anywhere → command-style search bar opens.
- [ ] Type a query → results appear; Enter/click navigates correctly.
- [ ] Semantic/folder search returns sensible matches (not just exact filename).

## 4. File-type viewers (#183, #190)
- [ ] **3D models** (.glb/.obj/.stl): model renders from **all angles** — does NOT show only one side or disappear when rotated.
- [ ] **Archives** (.zip / .gz / .7z / .tar.gz): opens an **archive explorer** listing inner files with correct per-type view.
- [ ] **.zip viewer uses the window well** — wide, not a narrow column with big empty margins; clean layout.
- [ ] **.epub**: size/progress **%** displays correctly.
- [ ] **Tiny archives**: header size label is sane (not a stray "0 KB" — minor, flag if you still see it).
- [ ] Spot-check a few other types (PDF, markdown, SVG/HTML safe view, CSV) — each renders cleanly and uses the window.

## 5. Left tool-bubble rail (in the big file preview)
- [ ] Open any image in the big preview. On the **left edge** you see **minimized bubbles**: Comments, Info, Text (OCR), Best-of.
- [ ] Bubbles are **minimized by default** — the file is the focus, not the tools.
- [ ] Clicking a bubble expands **only that one**; opening another collapses the previous (one open at a time).
- [ ] Each tool works from inside its bubble (no leftover full-width panels covering the file).

## 6. OCR + translate to ALL languages (#170, #193)
- [ ] Open an image with text → **Text** bubble → OCR extracts the text with regions/overlay.
- [ ] Translate control → language picker is **searchable** and lists **~100 languages** (NLLB-200).
- [ ] Pick a non-Latin target (e.g. Japanese, Arabic, Hindi) → translation returns (not just European languages).
- [ ] Auto-detect of the source language works (don't have to specify source).

## 7. Best-of anywhere + cursor highlight (#172)
- [ ] In the gallery, right-click / card menu → **"Find best of similar"** is available (not only inside an album).
- [ ] It surfaces visually-similar shots (burst/near-dupes) as a best-of set.
- [ ] While choosing the keeper, the **cursor-connected highlight** draws a connector line/outline from your cursor to the keeper.
- [ ] Picking a keeper resolves the set as expected.

## 8. Multi-select marquee (#182)
- [ ] In the gallery, click-drag to rubber-band select. The selection box **tracks your cursor exactly** — no drift/offset between the cursor and the box.
- [ ] Test at different scroll positions and after resizing the window (the page uses `zoom: 0.8`, which previously caused drift).
- [ ] Selected items are exactly the ones the box covers.

## 9. AI summaries for all file types — useful & searchable (#184)
*(Wait a few min for the backfill to finish first.)*
- [ ] Open a **code file** (e.g. one of the sample .py / .js / .java) → summary is **useful + descriptive** (e.g. "Python file for machine learning…"), not generic.
- [ ] Open an **archive** (.zip) → summary describes its contents meaningfully.
- [ ] Open **Office docs** — `.docx`, `.xlsx`, and `.pptx` each summarize their real content (the `.pptx` pulls slide titles, body text, and speaker notes — not just the filename).
- [ ] **Search** for a concept from a file's summary (e.g. "machine learning", "blackjack", "presentation") → the matching code/archive/doc files come up.
- [ ] Documents/notes/other types all have non-empty, searchable summaries.

## 10. SFTP — full feature (#169, #185, #186, #187, #188)
**Setup (easy path):**
- [ ] **Settings → SFTP** tab exists and gives a **guided, step-by-step** setup.
- [ ] Windows commands are **CMD-only** and live in a **clean dropdown menu** (not a wall of text).
- [ ] Commands show **your real email** as the username (per-user), and host `localhost`, port `2222` — no leftover placeholder like `…@localhost`.
- [ ] Copy the `ssh-keygen` + `clip` commands → generate the `neuthek` key → register the public key in Settings.

**Connect & branding:**
- [ ] `sftp -i %USERPROFILE%\.ssh\neuthek -P 2222 <youremail>@localhost` connects (banner shows the neuthek ASCII art).
- [ ] At the SFTP root you see a branded **neuthek folder icon** (desktop.ini + .ico) the way OneDrive shows its logo in Explorer.
- [ ] A read-only **"Welcome to neuthek.txt"** is present at root.

**Read + write + quota:**
- [ ] **Download** (`get`) a file — works (no 0-byte / cat failure).
- [ ] **Upload** (`put`) a file — appears in the web app under your account.
- [ ] Files are **isolated to your user** (you only see your own).
- [ ] **Quota test:** upload enough to exceed your storage limit, ideally **two uploads at once / a bulk put** → it should **fill up to your limit then reject the rest** (atomic, no over-fill), with a clear quota error (413) on the rejected ones.

## 11. Settings — clean & organized (#178, #189, #191, #192)
- [ ] Every section is **always visible and well-organized** — no collapsibles, nothing feels overwhelming.
- [ ] **No "About" tab.**
- [ ] Spacing, cards, and buttons look consistent and polished across **every tab**.
- [ ] **Autofill bug fixed:** focus a password field where the browser offers to autofill — your **email does NOT spill into the search bar**.
- [ ] Cloud Sync tab reads cleanly; SFTP tab is friendly (per #10).

## 12. Vault — viewers + import (zero-knowledge) (#106, #107)
- [ ] Unlock the vault. **Passwords** and **Notes** render in clean Apple-style viewers.
- [ ] **.vcf** files: a **vCard** shows as contact cards; a **genomics Variant Call Format** file shows as a **data table** (not "No contacts found").
- [ ] **Import from file:** import a passwords export (Chrome / Firefox / Bitwarden / 1Password / LastPass / KeePass CSV or JSON, or a notes file) → items import correctly with a summary of what was parsed.
- [ ] **Zero-knowledge intact:** imported items are encrypted client-side (the server never sees plaintext). (If you watch the network tab, the create-item payload is ciphertext only.)

## 13. iCloud dedup (#174)
- [ ] Trigger / re-run an iCloud sync → it does **not** pull duplicate copies of the same file.
- [ ] Existing library has no obvious dupes from earlier pulls (cleanup already merged true duplicates; burst shots that merely share a name are kept separate).

## 14. Iframe embed + login (cookie)
- [ ] If you embed the app in an iframe (e.g. the film/landing deck served over http://localhost), **login persists** — no "login 204 then /users/me 401" loop.
- [ ] Normal (non-iframe) login still works.

---

## 15. Person re-detection — "Find more photos of this person" (#98)
- [ ] **People** tab → click a **named** person to drill in. In the header there's a **"Find more photos"** button (shows only for named people, not unnamed clusters).
- [ ] Click it → a modal opens with a **candidate grid** of additional faces of that person found across your library (instant — it matches existing face embeddings, no re-scan wait).
- [ ] Candidates are **high-precision** (only unassigned faces above the similarity threshold) and it **never steals** a face already assigned to another named person.
- [ ] **Uncheck** any wrong faces, then **"Add N to &lt;name&gt;"** → those photos attach to the person and the person's count updates.
- [ ] **Reject** = just unchecking/closing (nothing is persisted for rejected ones; the face stays an unlabeled cluster).
- [ ] If some photos were never face-scanned, the modal indicates that (an `unscanned` count) so you know a backfill could surface even more.

---

## Removed (confirm it's gone)
- [ ] **Connections / smart graph** feature is fully removed — no "Connections" entry in the sidebar, no graph tab, no leftover broken UI.
