// Vault import-from-file (VLT-7) — CLIENT-SIDE parsing + mapping ONLY.
//
// When a user drops a structured DATA file into the vault, we parse it HERE,
// in the browser, and map each record to one of the vault's structured secure
// item plaintext shapes (contact / card / password / note). The caller
// (vault-panel.jsx → onPickFiles) then encrypts each mapped record with the
// vault key via @/vault/crypto and POSTs only ciphertext. NOTHING in this
// module touches a key, a nonce, the network, or the server — so the
// zero-knowledge guarantee is untouched: the server never sees this plaintext.
//
// Supported:
//   .vcf / vCard            → one `contact` item per vCard (a card-bearing
//                             vCard, i.e. one with KIND:card-ish payment
//                             fields, becomes a `card` item instead)
//   password export
//     .csv / .tsv           → one `password` item per row (browser / Bitwarden
//                             / 1Password / generic name,url,username,password)
//     .json                 → one `password` item per entry (Bitwarden export,
//                             generic array/object shapes)
//   payment-card file .json → one `card` item (single object or array)
//   SSH key
//     id_rsa / *.pem / etc. → one `ssh_key` item (PEM/OpenSSH private key)
//     *.pub                 → one `ssh_key` item (public key only)
//   .txt / .md / note       → one `note` item (whole file body)
//
// Anything we can't confidently map returns null → the caller falls back to
// the existing encrypted-file upload, so no existing behavior breaks.

import { parseVcf } from "./vcf-viewer.jsx";
import { parseDelimited, pickDelimiter } from "./csv-viewer.jsx";

// ---------------------------------------------------------------------------
// small shared helpers
// ---------------------------------------------------------------------------

function ext(name = "") {
  const i = name.lastIndexOf(".");
  return i === -1 ? "" : name.slice(i + 1).toLowerCase();
}

function baseName(name = "") {
  const slash = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  const justName = slash === -1 ? name : name.slice(slash + 1);
  const dot = justName.lastIndexOf(".");
  return dot <= 0 ? justName : justName.slice(0, dot);
}

function clean(v) {
  return typeof v === "string" ? v.trim() : v == null ? "" : String(v);
}

// A run of digits long enough to plausibly be a payment-card / account number.
// Used both to recognize a card-bearing vCard and to sanity-check card files.
function looksLikeCardNumber(s) {
  const digits = clean(s).replace(/[\s-]/g, "");
  return /^\d{12,19}$/.test(digits);
}

// ---------------------------------------------------------------------------
// vCard → contact (or card) items
// ---------------------------------------------------------------------------

// Map a single parsed vCard (from parseVcf) into a `contact` plaintext that
// mirrors what the vcf-viewer surfaces, but flattened to JSON-serializable
// fields the vault stores + the ContactView renders.
//
// Shape (the vault `contact` item plaintext):
//   { title, fullName, org, role, phones:[{value,label}], emails:[...],
//     addresses:[{value,label}], urls:[{value,label}], birthday, note }
function vcardToContact(card) {
  const fullName =
    clean(card.fn) ||
    [card.n?.given, card.n?.family].map(clean).filter(Boolean).join(" ") ||
    "";
  const title = fullName || clean(card.org) || "Contact";
  // Photos can be huge base64 blobs; a contact item is a small encrypted
  // record, so we deliberately drop the photo (the field stays text-only).
  return {
    title,
    fullName,
    org: clean(card.org),
    role: clean(card.title),
    phones: (card.phones || []).map((p) => ({ value: clean(p.value), label: clean(p.label) })),
    emails: (card.emails || []).map((e) => ({ value: clean(e.value), label: clean(e.label) })),
    addresses: (card.addrs || []).map((a) => ({ value: clean(a.value), label: clean(a.label) })),
    urls: (card.urls || []).map((u) => ({ value: clean(u.value), label: clean(u.label) })),
    birthday: clean(card.bday),
    note: clean(card.note),
  };
}

// Heuristic: a vCard that really describes a payment card (some exporters
// stuff card data into a vCard). We look for a card-number-shaped value in a
// TEL/NOTE/raw field. Conservative — defaults to treating a vCard as a contact.
function vcardLooksLikeCard(card) {
  const candidates = [
    card.note,
    ...(card.phones || []).map((p) => p.value),
  ];
  return candidates.some((c) => looksLikeCardNumber(c));
}

function vcardToCard(card) {
  const number =
    [card.note, ...(card.phones || []).map((p) => p.value)]
      .map(clean)
      .find((c) => looksLikeCardNumber(c)) || "";
  const name =
    clean(card.fn) ||
    [card.n?.given, card.n?.family].map(clean).filter(Boolean).join(" ");
  return {
    title: name ? `${name}'s card` : "Imported card",
    cardholder: name,
    number: number.replace(/[\s-]/g, ""),
    expiry: "",
    cvv: "",
    brand: clean(card.org),
  };
}

// Public: parse a .vcf body into structured items. Returns [] if no readable
// vCards (caller then falls back to file upload).
export function importVcards(text) {
  let cards;
  try {
    cards = parseVcf(text);
  } catch {
    return [];
  }
  if (!Array.isArray(cards)) return [];
  return cards.map((c) =>
    vcardLooksLikeCard(c)
      ? { kind: "card", data: vcardToCard(c) }
      : { kind: "contact", data: vcardToContact(c) },
  );
}

// ---------------------------------------------------------------------------
// password exports → password items
// ---------------------------------------------------------------------------

// Column-name aliases across the common exporters (browsers, Bitwarden,
// 1Password CSV, KeePass, LastPass). Lower-cased, punctuation-stripped keys.
const PW_FIELD_ALIASES = {
  title: ["name", "title", "account", "entry", "item", "label"],
  username: ["username", "user", "login username", "login name", "loginname", "email", "user name"],
  password: ["password", "pass", "login password", "pwd", "secret"],
  url: ["url", "uri", "uris", "website", "login uri", "login uris", "web site", "link", "site", "loginurl"],
  notes: ["notes", "note", "comment", "comments", "extra"],
};

function normKey(k) {
  return clean(k).toLowerCase().replace(/[\s_-]+/g, " ").trim();
}

// Build a header→canonical-field map. Returns null when the header doesn't
// expose at least a password column (so we don't misread an unrelated CSV).
function passwordHeaderMap(header) {
  const norm = header.map(normKey);
  const map = {};
  for (const [canonical, aliases] of Object.entries(PW_FIELD_ALIASES)) {
    const idx = norm.findIndex((h) => aliases.includes(h));
    if (idx !== -1) map[canonical] = idx;
  }
  return map.password != null ? map : null;
}

function rowToPassword(row, map, fallbackTitle) {
  const at = (k) => (map[k] != null ? clean(row[map[k]]) : "");
  const title = at("title") || at("url") || at("username") || fallbackTitle || "Imported login";
  return {
    title,
    username: at("username"),
    password: at("password"),
    url: at("url"),
    notes: at("notes"),
  };
}

// Parse a delimited (CSV/TSV) password export. Returns [] when it doesn't look
// like a credential export.
function importPasswordCsv(text, fallbackTitle) {
  const delim = pickDelimiter(text);
  const rows = parseDelimited(text, delim).filter((r) => r.some((c) => clean(c) !== ""));
  if (rows.length < 2) return [];
  const map = passwordHeaderMap(rows[0]);
  if (!map) return [];
  const out = [];
  for (let i = 1; i < rows.length; i++) {
    const pw = rowToPassword(rows[i], map, fallbackTitle);
    // Skip blank/again-header rows; require something secret-ish to import.
    if (!pw.password && !pw.username) continue;
    out.push({ kind: "password", data: pw });
  }
  return out;
}

// Map a single JSON login object (already key-aliased shapes from Bitwarden /
// generic exports) to a password plaintext. Bitwarden nests under `login`.
function jsonObjToPassword(obj, fallbackTitle) {
  if (!obj || typeof obj !== "object") return null;
  const login = obj.login && typeof obj.login === "object" ? obj.login : obj;
  const pick = (o, aliases) => {
    for (const key of Object.keys(o || {})) {
      if (aliases.includes(normKey(key))) {
        const v = o[key];
        if (Array.isArray(v)) {
          // Bitwarden `login.uris` = [{uri}]; `login.username` is a scalar.
          const first = v[0];
          return clean(first && typeof first === "object" ? first.uri || first.value : first);
        }
        return clean(v);
      }
    }
    return "";
  };
  const username = pick(login, PW_FIELD_ALIASES.username);
  const password = pick(login, PW_FIELD_ALIASES.password);
  const url = pick(login, PW_FIELD_ALIASES.url);
  const title = clean(obj.name) || pick(obj, PW_FIELD_ALIASES.title) || url || username || fallbackTitle || "Imported login";
  if (!password && !username) return null;
  return {
    title,
    username,
    password,
    url,
    notes: clean(obj.notes) || pick(obj, PW_FIELD_ALIASES.notes),
  };
}

// ---------------------------------------------------------------------------
// payment-card JSON → card items
// ---------------------------------------------------------------------------

const CARD_FIELD_ALIASES = {
  cardholder: ["cardholder", "cardholdername", "name", "holder", "card holder", "fullname"],
  number: ["number", "cardnumber", "card number", "pan", "ccnum", "card no", "cardno"],
  expiry: ["expiry", "expiration", "exp", "expdate", "expiration date", "valid thru", "validthru"],
  cvv: ["cvv", "cvc", "cvv2", "csc", "security code", "code"],
  brand: ["brand", "type", "issuer", "network", "card type"],
};

function jsonObjToCard(obj, fallbackTitle) {
  if (!obj || typeof obj !== "object") return null;
  const src = obj.card && typeof obj.card === "object" ? obj.card : obj;
  const pick = (aliases) => {
    for (const key of Object.keys(src)) {
      if (aliases.includes(normKey(key))) return clean(src[key]);
    }
    return "";
  };
  const number = pick(CARD_FIELD_ALIASES.number).replace(/[\s-]/g, "");
  if (!looksLikeCardNumber(number)) return null; // not actually a card object
  const cardholder = pick(CARD_FIELD_ALIASES.cardholder);
  return {
    title: clean(obj.name) || clean(obj.title) || (cardholder ? `${cardholder}'s card` : fallbackTitle) || "Imported card",
    cardholder,
    number,
    expiry: pick(CARD_FIELD_ALIASES.expiry),
    cvv: pick(CARD_FIELD_ALIASES.cvv),
    brand: pick(CARD_FIELD_ALIASES.brand),
  };
}

// ---------------------------------------------------------------------------
// JSON dispatcher (passwords or cards, single object or array, Bitwarden)
// ---------------------------------------------------------------------------

function importJson(text, fallbackTitle) {
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    return [];
  }
  // Normalize to a flat array of candidate record objects. Bitwarden wraps its
  // logins in `{ items: [...] }`; some tools use `{ logins: [...] }`.
  let records;
  if (Array.isArray(parsed)) records = parsed;
  else if (parsed && Array.isArray(parsed.items)) records = parsed.items;
  else if (parsed && Array.isArray(parsed.logins)) records = parsed.logins;
  else if (parsed && typeof parsed === "object") records = [parsed];
  else return [];

  const out = [];
  for (const rec of records) {
    // Prefer card when the record clearly carries a card number; else login.
    const card = jsonObjToCard(rec, fallbackTitle);
    if (card) {
      out.push({ kind: "card", data: card });
      continue;
    }
    const pw = jsonObjToPassword(rec, fallbackTitle);
    if (pw) out.push({ kind: "password", data: pw });
  }
  return out;
}

// ---------------------------------------------------------------------------
// SSH key files → ssh_key items
// ---------------------------------------------------------------------------

// Infer the SSH key type from the key text (private OR public). Mirrors the
// inferSshKeyType helper in vault-panel.jsx. Returns ed25519|rsa|ecdsa|dsa|"".
function inferSshKeyType(privateKey = "", publicKey = "") {
  const pub = String(publicKey || "");
  const priv = String(privateKey || "");
  if (/ssh-ed25519/i.test(pub)) return "ed25519";
  if (/ecdsa-sha2-/i.test(pub) || /BEGIN EC PRIVATE KEY/i.test(priv)) return "ecdsa";
  if (/ssh-dss/i.test(pub) || /BEGIN DSA PRIVATE KEY/i.test(priv)) return "dsa";
  if (/ssh-rsa/i.test(pub) || /BEGIN RSA PRIVATE KEY/i.test(priv)) return "rsa";
  const m = pub.match(/^(ssh-[a-z0-9]+|ecdsa-sha2-[a-z0-9-]+)/i);
  if (m) {
    if (/ed25519/i.test(m[1])) return "ed25519";
    if (/ecdsa/i.test(m[1])) return "ecdsa";
    if (/dss/i.test(m[1])) return "dsa";
    if (/rsa/i.test(m[1])) return "rsa";
  }
  return "";
}

// An OpenSSH/PEM private key header (covers OPENSSH/RSA/EC/DSA + generic
// PRIVATE KEY). Conservative — only a genuine private-key file matches.
const SSH_PRIVATE_RE =
  /-----BEGIN (?:OPENSSH|RSA|EC|DSA|ENCRYPTED)?\s*PRIVATE KEY-----/;
// A single-line OpenSSH PUBLIC key (ssh-ed25519 / ssh-rsa / ecdsa-… / ssh-dss).
const SSH_PUBLIC_RE = /^(?:ssh-(?:ed25519|rsa|dss)|ecdsa-sha2-[a-z0-9-]+)\s+[A-Za-z0-9+/]/m;

// Does this look like an SSH key file by NAME? id_rsa / id_ed25519 / *.pub /
// *.pem / *.key — the common on-disk names. Extensionless `id_*` files are the
// usual private-key case.
function looksLikeSshFilename(name = "") {
  const base = baseNameWithExt(name).toLowerCase();
  const e = ext(name);
  if (e === "pub" || e === "pem" || e === "key" || e === "ppk") return true;
  if (/^id_(rsa|dsa|ecdsa|ed25519)$/.test(base)) return true;
  if (base === "authorized_keys" || base === "known_hosts") return false; // not a single key
  return false;
}

function baseNameWithExt(name = "") {
  const slash = Math.max(name.lastIndexOf("/"), name.lastIndexOf("\\"));
  return slash === -1 ? name : name.slice(slash + 1);
}

// Map an SSH key file's text into a single `ssh_key` item, or [] when it isn't
// recognizably an SSH key. A private-key file becomes the privateKey; a `.pub`
// (or any standalone public-key line) becomes the publicKey.
function importSshKey(text, fallbackTitle, name) {
  const body = clean(text);
  if (!body) return [];
  const hasPrivate = SSH_PRIVATE_RE.test(body);
  // The FULL public-key line (prefix + base64 + optional comment), not just the
  // regex prefix — find the whole line so the comment/host survives.
  const pubLine = body.split(/\r?\n/).find((l) => SSH_PUBLIC_RE.test(l)) || "";
  if (!hasPrivate && !pubLine) return [];
  let privateKey = "";
  let publicKey = "";
  if (hasPrivate) {
    privateKey = body;
    // A combined file occasionally trails the public line after the block.
    if (pubLine) publicKey = clean(pubLine);
  } else {
    // Public-key-only file (.pub): keep just the public-key line.
    publicKey = clean(pubLine);
  }
  // A public key's 3rd whitespace-token is its comment, usually `user@host`.
  const comment = publicKey ? publicKey.trim().split(/\s+/)[2] || "" : "";
  return [
    {
      kind: "ssh_key",
      data: {
        title: fallbackTitle || comment || baseNameWithExt(name) || "SSH key",
        host: comment,
        keyType: inferSshKeyType(privateKey, publicKey),
        privateKey,
        publicKey,
        passphrase: "",
      },
    },
  ];
}

// ---------------------------------------------------------------------------
// plain text / markdown → note item
// ---------------------------------------------------------------------------

function importNote(text, fallbackTitle) {
  const body = text.replace(/^﻿/, ""); // strip BOM
  if (!body.trim()) return [];
  return [{ kind: "note", data: { title: fallbackTitle || "Imported note", body } }];
}

// ---------------------------------------------------------------------------
// top-level: file → structured items (or null to fall back to file upload)
// ---------------------------------------------------------------------------

// Decide whether a file's extension marks it as a candidate for structured
// import at all. Anything else (images, pdf, video, archives, office docs…)
// is NOT a data file we parse — it goes straight to encrypted file upload.
const STRUCTURED_EXTS = new Set(["vcf", "vcard", "csv", "tsv", "json", "txt", "text", "md", "markdown"]);

export function isImportableExt(name = "") {
  // SSH key files (id_rsa / *.pub / *.pem / *.key) are admitted by name so the
  // caller reads them as text; parseImportableFile then content-checks them.
  return STRUCTURED_EXTS.has(ext(name)) || looksLikeSshFilename(name);
}

// Parse a file's TEXT into an array of { kind, data } structured items, or
// return null when it shouldn't / can't be imported structurally (caller then
// uploads it as a normal encrypted file). Pure + synchronous — the caller has
// already read the text off the Blob.
//
//   name : original filename (drives extension routing + fallback titles)
//   text : the decoded UTF-8 contents
export function parseImportableFile(name, text) {
  const e = ext(name);
  const title = baseName(name);

  // SSH key → ssh_key item. Checked FIRST and by CONTENT (a PEM/OpenSSH key
  // header or a public-key line), so a key saved with any name — extensionless
  // id_rsa, *.pub, *.pem, or even key.txt — routes here. A file that merely has
  // a key-ish name but no key content falls through to the normal handlers.
  if (looksLikeSshFilename(name) || SSH_PRIVATE_RE.test(text) || SSH_PUBLIC_RE.test(text)) {
    const items = importSshKey(text, title, name);
    if (items.length) return items;
  }

  // vCard → contacts / cards
  if (e === "vcf" || e === "vcard") {
    const items = importVcards(text);
    return items.length ? items : null;
  }

  // JSON → password / card exports
  if (e === "json") {
    const items = importJson(text, title);
    return items.length ? items : null;
  }

  // CSV / TSV → only import when it's recognizably a password export; an
  // arbitrary spreadsheet CSV is NOT a secret and should upload as a file.
  if (e === "csv" || e === "tsv") {
    const items = importPasswordCsv(text, title);
    return items.length ? items : null;
  }

  // Plain text / markdown → secure note
  if (e === "txt" || e === "text" || e === "md" || e === "markdown") {
    const items = importNote(text, title);
    return items.length ? items : null;
  }

  return null;
}

// How many bytes we're willing to read as text for structured import. A
// genuine contact/password/card/note export is tiny; anything larger is
// almost certainly a real file (e.g. a big .json data dump or .txt log) and
// is better stored as an encrypted file than exploded into vault items.
export const MAX_IMPORT_TEXT_BYTES = 2 * 1024 * 1024; // 2 MiB
