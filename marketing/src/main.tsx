import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

// BrowserRouter (not HashRouter) so each marketing page lives at a
// clean URL like `/updates/2026-w20-foo` instead of `/#/updates/...`.
// Cleaner URLs make Google + Bing + Perplexity / Claude / GPT browse
// the site like any other content site — hash fragments aren't
// indexed reliably and don't appear in SERP snippets. The catch-all
// in public/_redirects sends every unknown path back to index.html
// so direct loads + refreshes still hit the SPA shell.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
