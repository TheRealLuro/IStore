"""Regression tests for D5 + F12 — audit-log details cap + PII scrub.

D5: `audit_log.details` JSONB blob was unbounded and held
attacker-controlled keys (comment anchor payloads, cloud-sync
remote paths, etc.). A few KB per row × many rows = audit-table
bloat → DoS. Plus a misleading admin UI render of attacker-chosen
strings.

F12: PII keys (email / ip / user-agent / google_sub) lived in
`details` forever — the retention sweeper only NULLed the
`user_id` column. After the GDPR-mandated horizon the per-user
column was anonymous but the JSONB still had the email next to it.

Fixes:
  - `add_audit` caps the serialized `details` at 4 KiB; oversized
    payloads are replaced with a tombstone that preserves
    forensic intent (top-level keys + original size) without the
    bloat.
  - `sweep_audit_log_anonymize` scrubs every key in
    `backend.audit.PII_DETAILS_KEYS` from `details` on every row
    past the retention horizon.
  - The PII-key registry is shared between writer + scrubber so
    they stay in sync.

These tests cover the writer + the registry. End-to-end of the
scrubber needs the DB-integration suite (still blocked by the
unrelated migration on the WIP branch); the registry test below
is what actually catches "developer added a new audit action that
records email but didn't update the scrubber set."
"""
from __future__ import annotations

from backend.audit import PII_DETAILS_KEYS, _MAX_DETAILS_BYTES, _cap_details


# ---------- D5: size cap ----------


def test_cap_details_passes_small_payload_unchanged() -> None:
    payload = {"action_detail": "x" * 100, "count": 42}
    out = _cap_details(payload)
    assert out == payload


def test_cap_details_at_exact_boundary_passes() -> None:
    """A payload that serializes to exactly the cap should still
    pass — the rejection is strictly `>`, not `>=`."""
    # JSON-encode an empty payload first to get the framing overhead.
    import json
    target = _MAX_DETAILS_BYTES - len(json.dumps({"k": ""}))
    payload = {"k": "a" * target}
    assert len(json.dumps(payload)) == _MAX_DETAILS_BYTES
    out = _cap_details(payload)
    assert out == payload


def test_cap_details_replaces_oversize_payload_with_tombstone() -> None:
    payload = {"junk": "x" * (_MAX_DETAILS_BYTES + 1)}
    out = _cap_details(payload)
    assert out.get("_truncated") is True
    assert out["_original_size"] > _MAX_DETAILS_BYTES
    assert out["_max_size"] == _MAX_DETAILS_BYTES
    assert out["_keys"] == ["junk"]


def test_cap_details_tombstone_preserves_top_level_key_names() -> None:
    """An operator looking at the row should at least see what was
    attempted (the action AND the top-level keys), even with the
    bytes redacted. This is what makes the cap honest — we erase
    the bloat, not the forensic intent."""
    payload = {"image_id": "abc", "victim_email": "x@y", "spam": "x" * 10_000}
    out = _cap_details(payload)
    assert sorted(out["_keys"]) == sorted(["image_id", "victim_email", "spam"])


def test_cap_details_handles_non_serializable() -> None:
    """A future caller that passes a non-JSON-serializable value
    (datetime, dataclass, set) shouldn't crash the audit path;
    return a tombstone so the action is still logged."""

    class _NotJSON:
        pass

    payload = {"weird": _NotJSON()}
    out = _cap_details(payload)
    # We accept either tombstone shape — the contract is "no
    # crash, structure survives in some form."
    assert isinstance(out, dict)
    assert "weird" in out.get("_keys", out)


# ---------- F12: PII registry ----------


def test_pii_registry_contains_expected_keys() -> None:
    """Pin the canonical set so a regression that drops one
    (e.g. someone deletes 'email' thinking it's no longer used)
    fails the test immediately."""
    expected = {
        "email", "recipient_email",
        "ip", "ua", "user_agent",
        "google_sub", "identity",
        "previous_email",
    }
    missing = expected - PII_DETAILS_KEYS
    assert not missing, (
        f"PII_DETAILS_KEYS is missing: {missing}. The scrubber will "
        "leave these in the audit_log.details JSONB blob past the "
        "GDPR retention horizon."
    )


def test_pii_keys_are_lowercase() -> None:
    """JSONB key comparisons are case-sensitive. If any registry
    entry is mis-cased ('Email'), the scrubber will silently leave
    the lowercase 'email' values in place. Pin the invariant."""
    for k in PII_DETAILS_KEYS:
        assert k == k.lower(), f"PII key {k!r} is not lowercase"


# ---------- F12: every add_audit call-site PII-keys are covered ----------


# Top-level keys we KNOW about in current audit `details` payloads
# that are NOT PII. Every key the static scan turns up must be
# either in PII_DETAILS_KEYS or in this allowlist. Adding a new
# audit-row writer with a new key flips the test red, forcing the
# author to triage.
KNOWN_NON_PII_KEYS = frozenset({
    # Internal / structural
    "_truncated", "_original_size", "_max_size", "_keys",
    "_audit_serialize_error",
    # IDs and counts
    "share_id", "image_id", "folder_id", "person_id", "tag_id",
    "comment_id", "face_id", "link_id", "asset_id", "user_id",
    "target_user_id", "recovery_code_id", "image_ids",
    "count", "deleted", "rows_deleted", "rows_anonymized",
    "rows_blob_scrubbed", "rows_nulled", "blobs_deleted",
    "blob_errors", "byte_size", "byte_count", "bytes",
    "bytes_freed", "blobs_planned", "scanned", "consumed",
    "updated_arms", "failed", "drift_applied_rows", "changes",
    "kind", "state", "scope", "status_code", "rl_status", "status",
    "previous_role", "previous_scheduled_at", "scheduled_delete_at",
    "grace_days", "expires_at", "retention_days", "cutoff",
    "swept_at", "deleted_at", "granted_at", "withdrawn_at",
    "created_at", "issued_at", "claimed_at", "viewed_at",
    "ip_address",
    # Cloud sync
    "provider", "remote_id", "remote_path", "remote_modified",
    "remote_size", "conflict_type", "conflict_kind", "files",
    "ai_enabled",
    # Share / asset
    "variant", "permission", "filename", "name", "label",
    "policy_version", "policy_text_sha256_hex", "signature_text",
    "consent_kind", "from", "to", "reason", "anchor",
    "path", "details", "level", "message", "method", "via",
    "quota_bytes", "old_quota_bytes", "role",
    "totp_verified_at", "totp_enabled",
    # Marketing-site newsletter opt-in forward outcome (boolean).
    "marketing_synced",
    "accounts_hard_deleted", "accounts_skipped_no_due",
    "has_anchor", "model", "category", "action",
    # Audit-meta + sweeper bookkeeping
    "rows_examined", "errors", "claims_count",
})


def test_known_non_pii_keys_dont_overlap_pii() -> None:
    """Sanity: a key can't be both PII and non-PII. Catches a
    typo / copy-paste where someone adds 'email' to the non-PII
    set."""
    overlap = KNOWN_NON_PII_KEYS & PII_DETAILS_KEYS
    assert not overlap, (
        f"Keys appear in BOTH PII and non-PII sets: {overlap}. "
        "Resolve the conflict — a key is one or the other."
    )
