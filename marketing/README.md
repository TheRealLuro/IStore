# neuthek marketing

Static onboarding + marketing site for neuthek. Vite + React +
TypeScript, Geist fonts, no Tailwind. Designed to deploy to Render
as a fully static site so we can wire Stripe and a real backend
into the main repo without touching this surface.

## Local dev

```bash
cd marketing
npm install
npm run dev      # http://127.0.0.1:5180
```

## Build + preview

```bash
npm run build
npm run preview  # http://127.0.0.1:4180
```

## Deploy to Render

This folder ships a Render blueprint at `render.yaml`. Either:

- Point the Render dashboard at this repo and select the blueprint, or
- `render blueprint deploy --root marketing` from the CLI.

The site is fully static. There are no env secrets required, and
no backend is called from these pages today. The waitlist form
saves to `localStorage` only — see the inline comment in
`src/pages/Waitlist.tsx` for the rollout plan.

## Layout

```
marketing/
  index.html
  package.json
  vite.config.ts
  render.yaml
  public/
    favicon.svg
    robots.txt
    _redirects        <- SPA fallback for static hosts
  src/
    main.tsx
    App.tsx
    styles.css
    components/
      Banner.tsx       <- persistent "hosted not live yet"
      Nav.tsx
      Footer.tsx
      WordMark.tsx     <- animated logo
      TechCarousel.tsx <- revolving stack ribbon
    pages/
      Home.tsx
      Features.tsx
      Hosting.tsx
      Developers.tsx
      Roadmap.tsx
      Compare.tsx
      Pricing.tsx
      Waitlist.tsx
      Privacy.tsx
      Terms.tsx
      NotFound.tsx
```

## Editing rules

- Every claim about the product must be true today, or marked
  "planned" / "coming soon" — no aspirational marketing language
  presented as shipped behavior.
- Competitor mentions are nominative only. No vendor logos that
  aren't ours; brand names quoted as text.
- The persistent `Banner` reminding visitors that the hosted
  version is not live must stay until the hosted launch.
