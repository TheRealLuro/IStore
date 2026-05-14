// Shared labeling for audit-log rows. Both the Account → Activity log
// panel (account.jsx) and the in-modal expanded view (account-panels.jsx)
// render entries; without a shared helper the two drift and one path
// (account.jsx) was rendering raw action codes like
// "consent.bandit_compression_telemetry.grant" instead of a friendly
// English label.

// Direct-match codes. Anything not present falls through to the pattern
// handlers below.
const ACTIVITY_LABELS = {
  "auth.login.success":   "Signed in",
  "auth.login.succeeded": "Signed in",
  "auth.login.failure":   "Failed sign-in attempt",
  "auth.login.failed":    "Failed sign-in attempt",
  "auth.logout":          "Signed out",
  "auth.register":        "Account created",
  "auth.password_change": "Password changed",
  "auth.password.reset":  "Password reset",
  "image.upload":         "Uploaded a file",
  "image.delete":         "Deleted a file",
  "image.bulk_delete":    "Deleted files",
  "image.rename":         "Renamed a file",
  "image.move":           "Moved a file",
  "image.star":           "Starred a file",
  "image.unstar":         "Unstarred a file",
  "image.restore":        "Restored a file from trash",
  "people.name_cluster":  "Named a person",
  "people.rename":        "Renamed a person",
  "people.delete":        "Removed a person",
  "people.rescan_all":    "Re-scanned faces in library",
  "account.export":       "Exported your data",
  "account.delete":       "Deleted account data",
  "account.images.delete": "Deleted your images",
  "account.trash.empty":  "Emptied the trash",
  "folder.create":        "Created a folder",
  "folder.rename":        "Renamed a folder",
  "folder.delete":        "Deleted a folder",
};

const CONSENT_SCOPE_LABELS = {
  face_recognition:              "face recognition",
  ai_summary:                    "AI summaries",
  semantic_search:               "semantic search",
  gps_retention:                 "location retention",
  exif_retention:                "EXIF retention",
  bandit_compression_telemetry:  "compression telemetry",
};

export function activityTone(action) {
  if (!action) return "blue";
  if (action.startsWith("auth.")) return "green";
  if (action.includes("delete") || action.includes("withdraw") || action.includes("failed")) return "orange";
  return "blue";
}

function humanScope(scope) {
  if (!scope) return "a scope";
  return CONSENT_SCOPE_LABELS[scope] || String(scope).replace(/_/g, " ");
}

export function activityLabel(action, details) {
  if (ACTIVITY_LABELS[action]) {
    const base = ACTIVITY_LABELS[action];
    if (action === "image.bulk_delete" && details?.count) {
      return `Deleted ${details.count} file${details.count === 1 ? "" : "s"}`;
    }
    if (action === "people.name_cluster" && details?.display_name) {
      return `Named a person "${details.display_name}"`;
    }
    if (action === "people.rename" && details?.display_name) {
      return `Renamed a person to "${details.display_name}"`;
    }
    return base;
  }
  // consent.<scope>.grant / .withdraw — scope is encoded in the action.
  const consentMatch = /^consent\.([a-z0-9_]+)\.(grant|withdraw)$/.exec(action || "");
  if (consentMatch) {
    const [, scope, verb] = consentMatch;
    return verb === "grant"
      ? `Enabled ${humanScope(scope)}`
      : `Disabled ${humanScope(scope)}`;
  }
  if (action === "consent.grant") return `Enabled ${humanScope(details?.scope)}`;
  if (action === "consent.withdraw") return `Disabled ${humanScope(details?.scope)}`;
  // Last resort: title-case the raw action so it stays readable even
  // for codes we haven't mapped yet.
  return (action || "").replace(/[_.]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function activityWhen(iso) {
  try {
    const d = new Date(iso);
    const diffSec = (Date.now() - d.getTime()) / 1000;
    if (diffSec < 60) return "Just now";
    if (diffSec < 3600) return `${Math.round(diffSec / 60)} min ago`;
    if (diffSec < 86400) return `${Math.round(diffSec / 3600)} h ago`;
    if (diffSec < 86400 * 7) return `${Math.round(diffSec / 86400)} d ago`;
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}
// hmr test 1778756531
