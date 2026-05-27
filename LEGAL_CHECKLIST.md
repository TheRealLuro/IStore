# neuthek — operator legal-action checklist

Concrete steps you (the operator) need to do **outside the codebase** before
neuthek is legally clean to open for public signups. Every item links back to
the page on the marketing site that already references it, so the policies and
the operational work stay in sync.

The checklist is grouped by urgency. **🔴 = block public launch.** **🟡 =
needed before EU/UK signups.** **🟢 = needed before paid signups.** **⚪ =
within 6 months of launch, but not blocking.**

Last updated: 2026-05-27. When you complete an item, move it to the
**Done** section at the bottom and update the date.

---

## 🔴 BEFORE PUBLIC LAUNCH (any signups beyond the current waitlist)

### L1 — Form a legal entity
- **What:** Form a US LLC or C-corp. Pick a state (Delaware is the default for tech, your home state is fine for a solo LLC).
- **Why:** Personal-liability shield. Required for the bank account that processes payments later. Privacy + Legal Notice + Terms currently say "individual developer, pre-incorporation" — that becomes the entity name on day 1.
- **Cost:** $0–$500 depending on state + service. Wyoming LLC ≈ $100/yr; Delaware C-corp ≈ $300 to form + $300/yr franchise tax.
- **Time:** 1–2 weeks. Use Stripe Atlas, Firstbase, or Clerky if you want a turnkey C-corp; LegalZoom or BizFilings for an LLC.
- **After:** Update `marketing/src/pages/Privacy.tsx` controller block, `LegalNotice.tsx`, `Terms.tsx` governing-law section, and Footer postal address.

### L2 — Register the DMCA Designated Agent with the US Copyright Office
- **What:** Designate an agent to receive copyright takedown notices, register them via the DMCA Designated Agent Directory.
- **Why:** Required by 17 U.S.C. §512(c)(2) to claim the §512 safe harbor. Without it, you have no defense against direct liability when a user uploads infringing content.
- **Cost:** $6 to register, $6 to renew every 3 years.
- **Time:** ~30 minutes online.
- **URL:** https://dmca.copyright.gov/
- **After:** Update `marketing/src/pages/Dmca.tsx` with the registered agent name + postal address + the directory URL for your specific record.

### L3 — Run the privacy + face-recognition DPIA
- **What:** Document a Data Protection Impact Assessment (GDPR Art. 35). Required because face recognition is on the EDPB's mandatory DPIA list (large-scale Art. 9 processing).
- **Why:** Required by GDPR Art. 35. If you process biometric data of EU users without a DPIA, supervisory authorities will fine you on first complaint.
- **Cost:** $0 if you write it yourself (~8 hours). $1,500–$5,000 if you use a privacy law firm or specialist DPO.
- **Time:** 1–2 weeks elapsed.
- **Template:** ICO's online DPIA template at https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/accountability-and-governance/data-protection-impact-assessments-dpias/
- **After:** Internal document — no public page update, but reference the DPIA's existence in Privacy §3.

### L4 — Integrate CSAM hash matching
- **What:** Hook PhotoDNA, Thorn Safer, or Cloudflare's CSAM Scanning Tool into the upload pipeline so known CSAM hashes are blocked at the upload boundary and matches are auto-reported to NCMEC.
- **Why:** 18 U.S.C. §2258A creates an obligation to report CSAM the moment you become aware of it. Modern hosting providers (and your payment processor) will require proactive hash matching, not reactive reporting.
- **Cost:** Free if you use Cloudflare (their CSAM Scanning Tool is free for all Cloudflare customers); PhotoDNA is free via Microsoft after a vetting application; Thorn Safer is enterprise-tier paid.
- **Time:** Cloudflare = ~1 day to wire up; PhotoDNA = 2–6 weeks vetting + 1 week integration.
- **URL:** https://blog.cloudflare.com/the-csam-scanning-tool/ or https://www.microsoft.com/en-us/photodna
- **After:** Add a sub-section to `marketing/src/pages/Aup.tsx` confirming "uploads scanned against the NCMEC hash database via [provider]."

### L5 — External privacy + security review
- **What:** Hire an outside developer to read the code, an outside security person to penetration-test, and an outside privacy person to audit the data flows + policies. Document findings, fix what they find.
- **Why:** Privacy + Roadmap pages publicly commit to this. The "we don't train AI on your content" + "no E2E claims until E2E ships" + "BIPA-compliant face rec" all need outside verification before they hold up under FTC §5 scrutiny.
- **Cost:** $5k–$25k depending on scope + people. Smaller firms or independents are usually fine for a pre-launch product.
- **Time:** 4–8 weeks.
- **After:** Publish a short summary of findings on `/updates` (under a "Security audit" article) — *not* the raw findings, just "audited by X, Y issues found, Y fixed."

### L6 — Insurance: Cyber Liability + Errors & Omissions
- **What:** Bind a Cyber Liability + Tech E&O policy before the hosted service opens.
- **Why:** Most enterprise customers contractually require it (typical floor: $1M Cyber + $1M E&O). It also protects you personally during the BIPA + GDPR exposure window.
- **Cost:** $1,500–$5,000/yr for a $1M/$1M policy at neuthek's pre-revenue scale. Embroker, Vouch, or Coalition are the usual carriers for SaaS.
- **Time:** 1–2 weeks application + binding.
- **After:** Internal — no public page update.

### L7 — Stand up the data-subject request (DSAR) intake workflow
- **What:** Wire `privacy@neuthek.com` to a real inbox you check daily. Set up a simple workflow: triage → identity verification → response template → 30-day timer.
- **Why:** GDPR Art. 12(3) gives you one month to respond to a DSAR; CCPA gives 45 days. Missing the deadline is what triggers regulator complaints.
- **Cost:** $0 (use the email inbox you already have) or $100–$500/mo if you adopt a DSAR-management tool (OneTrust, Osano, DataGrail).
- **Time:** 1 day to set up the workflow + identity-verification template.
- **After:** Privacy page already references this — no update needed.

### L8 — Breach response runbook
- **What:** Write a documented incident response runbook: detection → triage → notification templates → regulator contact list.
- **Why:** GDPR Art. 33 = 72 hours to notify supervisory authority. State breach laws = various windows. Improvised response under 72-hour pressure goes badly.
- **Cost:** $0 (~4 hours of writing).
- **Time:** 1 day.
- **After:** Internal — no public page update.

---

## 🟡 BEFORE OPENING EU / UK WAITLIST (or any data collection from EU/UK)

The marketing-site waitlist accepts EU/UK email addresses today, which means the items below are
**already triggered if you don't geo-block EU/UK signups.** Either geo-block via Cloudflare until the items below are done, or do the items below.

### L9 — Appoint an EU GDPR Article 27 representative
- **What:** Contract with a service that acts as your EU representative.
- **Why:** GDPR Art. 27 requires any non-EU controller processing EU residents' personal data to have a written EU rep with a public address.
- **Cost:** ~€500–€2,000/yr. **Prighter** (€39–€199/mo), **EDPO** (~€1,500/yr), **VeraSafe** (~$2,000/yr) are the usual options.
- **Time:** 1–3 days to sign + get the contact details.
- **After:** Update `marketing/src/pages/LegalNotice.tsx` (EU representative block) and `Privacy.tsx` (controller section).

### L10 — Appoint a UK GDPR Article 27 representative
- **What:** Same as L9 but a UK-based provider. Post-Brexit, the EU rep does **not** cover the UK.
- **Cost:** ~£300–£1,500/yr. **Prighter** sells a UK-rep add-on; **VeraSafe UK** is the other common pick.
- **Time:** 1–3 days.
- **After:** Update `LegalNotice.tsx` + `Privacy.tsx` with the UK rep contact.

### L11 — Sign DPAs with subprocessors
- **What:** Counter-sign the Data Processing Addenda published by Render, Cloudflare, and (when added) Resend.
- **Why:** GDPR Art. 28 requires a written agreement with every subprocessor. Without it, transferring EU data to them is itself unlawful regardless of the SCCs.
- **Cost:** $0.
- **Time:** ~1 hour total.
- **Links:**
  - Render — visible in your Render dashboard once you're on a paid plan
  - Cloudflare — https://www.cloudflare.com/cloudflare-customer-dpa/
  - Resend (when added) — https://resend.com/legal/dpa
- **After:** Subprocessors page already lists them — no update needed.

### L12 — Cookie consent banner (only if/when you add any non-essential storage)
- **What:** If you add Google Analytics, Plausible, Mixpanel, ad pixels, or any non-essential storage, ship a consent banner with equal-prominence Accept/Reject and per-category granularity, blocking the third-party scripts until consent.
- **Why:** ePrivacy Dir. Art. 5(3) + UK PECR Reg. 6 + CNIL/ICO guidance. The site currently sets no cookies → no banner needed. The Cookies page documents this and commits to adding a banner if/when that changes.
- **Cost:** $0 (Cookiebot/Klaro/Termly free tiers are fine at this scale) or $20–$200/mo for paid CMPs.
- **Time:** 1–2 days when triggered.

---

## 🟢 BEFORE PAID SIGNUPS

### L13 — Stripe Atlas / payment processor onboarding
- **What:** Set up Stripe (or whichever you pick) for the hosted tier. KYB documentation, bank account, tax info.
- **Why:** You can't take money without a payment processor + a verified business identity.
- **Cost:** $0 to apply, Stripe takes 2.9% + 30¢ per US transaction.
- **Time:** 1 week if your entity (L1) is already formed.
- **After:** Update `Terms.tsx` to add a "Fees and billing" section once you know your tiers.

### L14 — Google CASA security assessment
- **What:** Annual security assessment required to keep restricted Google API scopes (i.e. `drive.readonly` for the cloud-sync feature).
- **Why:** Google's API Services User Data Policy. Without the assessment, your OAuth verification can be revoked.
- **Cost:** Free for tier 2 (self-assessment), $4,500–$15,000/yr for tier 3 (third-party assessor) — neuthek will be tier 2 to start, tier 3 once usage scales.
- **Time:** 2–6 weeks first time.
- **URL:** https://cloud.google.com/security/compliance/casa
- **After:** Mention completion in `/privacy` §6 (Google Limited Use affirmation already there).

### L15 — Set tiers + prices on the Hosting page
- **What:** Decide the actual plans + prices and publish on `/hosting`. The page currently says "Pricing announced soon" — that becomes the launch announcement.
- **Why:** Required to actually take money, and to set user expectation before they sign up.
- **Cost:** $0 — just deciding.
- **Time:** Internal modeling, plus 30 minutes to update the page.

---

## ⚪ WITHIN 6 MONTHS OF LAUNCH

### L16 — Appoint a Data Protection Officer (if Art. 37(1)(c) triggers)
- **What:** Document a DPO appointment, or document why one isn't required.
- **Why:** GDPR Art. 37(1)(c) — large-scale processing of Art. 9 (biometric) data triggers a mandatory DPO. Face recognition probably triggers it if you reach 5k+ EU users.
- **Cost:** $0 if you appoint yourself (allowed for SMEs), $500–$3,000/mo for a fractional external DPO (Prighter, DPO Centre, OneTrust).
- **Time:** 1 week.
- **After:** Update `Privacy.tsx` DPO block.

### L17 — Records of Processing Activities (ROPA, Art. 30)
- **What:** Maintain internal documentation of purposes, categories of data, recipients, transfers, retention, security. Updated when the processing changes.
- **Why:** GDPR Art. 30 requires it. Supervisory authorities ask for ROPA first thing in any audit.
- **Cost:** $0 (~16 hours initial + a few hours per quarter).
- **Time:** 2 weeks elapsed.
- **Template:** ICO + CNIL both publish free Excel templates.

### L18 — Adopt SOC 2 Type II (enterprise-ready)
- **What:** Hire an auditor to certify SOC 2 Type II.
- **Why:** Effectively required for most enterprise deals once you have any. Not for solo / consumer launch.
- **Cost:** $15k–$50k Type I, $40k–$100k Type II.
- **Time:** 6–12 months.

### L19 — Coordinated Vulnerability Disclosure (CVD) policy
- **What:** Publish `security@neuthek.com` + a CVD policy on `/security` (page not yet created — create as part of this).
- **Why:** Public commitment to safe-harbor for ethical hackers. Without it, well-meaning researchers don't report bugs to you.
- **Cost:** $0 (write the policy) or join HackerOne's free Disclose tier.
- **Time:** 1 day.
- **After:** Create `/security` page in `marketing/src/pages/Security.tsx`.

---

## How AI engines will surface this

Most of these items also need to be **visible** on the marketing site, not just in this private doc, so that AI answer engines (ChatGPT, Claude, Perplexity, Google AI Overview) can lift them when users ask questions like *"is neuthek GDPR compliant?"* or *"does neuthek scan for CSAM?"* The current Privacy + AUP + DMCA + Subprocessors + Legal Notice pages already reference every item above — when you complete one, update the corresponding page to remove the "to be appointed" / "before public launch" caveat.

---

## Done

(Move completed items here with the date they landed.)

- *(none yet)*
