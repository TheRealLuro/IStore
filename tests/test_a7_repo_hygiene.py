"""§A7 — repo-hygiene invariants enforced as pytest assertions.

These run in the normal pytest pass so a regression (a contributor
committing a real fixture or forgetting to .gitignore something) is
caught immediately, alongside the CI gitleaks step. The CI workflow
is the public defense; this test is the dev-loop one.

What we assert:

1. No binary fixtures land under `tests/` — every test should build
   its inputs in-process via `_png_bytes()` / `_docx_bytes()` /
   similar helpers.
2. The compliance docs exist and are non-empty.
3. The CI workflow includes a gitleaks job (so a contributor can't
   remove the secret scan without it showing up here too).
4. The .gitignore blocks the canonical sensitive-payload patterns
   (.env, *.dump, *.pem, model weights).
5. Tests don't reference any "real_*" / "prod_*" path literals.
6. The append-only audit-log trigger exists in a migration (the
   trigger is what makes consent records tamper-evident).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Binary extensions that must never be checked in under tests/.
# Each one of these is a class of file a contributor might accidentally
# add as a "real test fixture" (real photo, real PDF, real DB dump).
_BANNED_TEST_EXTS = (
    ".jpg", ".jpeg", ".png", ".heic", ".gif", ".mp4", ".mov",
    ".pdf", ".dump", ".sql", ".sqlite", ".sqlite3", ".db",
    ".bak", ".dump.gz", ".sql.gz",
)


def test_tests_dir_has_no_binary_fixtures():
    """Every file under tests/ should be a .py or a .txt / .md helper.
    Binary fixtures must be generated in-process. The CI workflow
    `synthetic-fixtures` enforces this on every push too."""
    tests_dir = REPO_ROOT / "tests"
    bad: list[str] = []
    for p in tests_dir.rglob("*"):
        if not p.is_file():
            continue
        # __pycache__ doesn't show up under git, but defend against
        # weird CI checkouts that pulled compiled bytecode.
        if "__pycache__" in p.parts:
            continue
        # node_modules under tests would be very surprising, but if
        # someone vendored a dep here, skip it.
        if "node_modules" in p.parts:
            continue
        suffix = p.suffix.lower()
        if suffix in _BANNED_TEST_EXTS:
            bad.append(str(p.relative_to(REPO_ROOT)))
    assert not bad, (
        "Binary fixtures detected under tests/. They must be generated "
        "in-process (see `_png_bytes`, `_docx_bytes`, "
        "`insert_face`):\n  " + "\n  ".join(bad)
    )


def test_compliance_docs_exist_and_have_content():
    """A6 deliverables: PRIVACY.md / SECURITY.md / DATA_PROCESSING.md
    must exist and carry more than a stub. Length floor is generous
    (1KB) so we catch "the file is gone" but not "we trimmed it"."""
    required = ("PRIVACY.md", "SECURITY.md", "DATA_PROCESSING.md", "REPO_HYGIENE.md", "TERMS.md")
    missing: list[str] = []
    short: list[str] = []
    for name in required:
        path = REPO_ROOT / name
        if not path.exists():
            missing.append(name)
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        if len(body) < 1024:
            short.append(f"{name} ({len(body)} bytes)")
    assert not missing, f"Missing compliance docs: {missing}"
    assert not short, f"Compliance docs too short (stub-like): {short}"


def test_security_doc_contains_disclosure_email():
    """SECURITY.md must surface a vulnerability-disclosure address so
    researchers know where to send findings."""
    body = (REPO_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    assert "security@neuthek.app" in body or "@" in body, (
        "SECURITY.md must list a disclosure email under "
        "'Reporting a vulnerability'."
    )
    assert "Supported versions" in body, (
        "SECURITY.md must include a 'Supported versions' section."
    )


def test_privacy_doc_covers_required_topics():
    """PRIVACY.md must address each topic listed in todo.md A6:
    what we collect, why, retention, deletion, embeddings, biometrics."""
    body = (REPO_ROOT / "PRIVACY.md").read_text(encoding="utf-8").lower()
    required_topics = (
        "collect",
        "retention",
        "delete",        # deletion / delete
        "embedding",     # embeddings
        "biometric",     # biometric / biometrics
        "consent",       # consent log
        "cookie",        # cookie / localStorage discussion
        "age",           # age gate
    )
    missing = [t for t in required_topics if t not in body]
    assert not missing, (
        f"PRIVACY.md missing required A6 topics (substrings): {missing}"
    )


def test_ci_runs_gitleaks():
    """A7 mandates a CI step that fails on committed secrets.
    Removing it should fail this test too."""
    wf = (REPO_ROOT / ".github" / "workflows" / "security.yml").read_text(
        encoding="utf-8"
    )
    assert "gitleaks" in wf.lower(), (
        "security.yml CI workflow must include the gitleaks job."
    )
    assert "fetch-depth: 0" in wf, (
        "gitleaks must run against full history (fetch-depth: 0)."
    )


def test_gitignore_blocks_canonical_sensitive_patterns():
    """The .gitignore must block the obvious leak classes per A7."""
    body = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    required_patterns = (
        ".env",         # secrets
        "data/",        # runtime payloads
        "*.dump",       # DB dumps
        "*.pem",        # private keys
        "*.key",
        "*.age",        # encrypted backups
        "node_modules/",
        "*.safetensors",  # model weights
    )
    missing = [p for p in required_patterns if p not in body]
    assert not missing, (
        f".gitignore missing A7-required patterns: {missing}"
    )


def test_no_real_or_prod_data_paths_referenced_in_tests():
    """Tests must not reference real_* / prod_* / production_* path
    literals. The string `production` is allowed in function names
    like `validate_production_settings` — we only flag file-path-shaped
    occurrences."""
    tests_dir = REPO_ROOT / "tests"
    bad_lines: list[str] = []
    # Match path-literal patterns like "real_users.dump",
    # "prod_passport.jpg", "production_export.sql".
    pat = re.compile(
        r'["\'](real_[\w./-]+|prod_[\w./-]+|production_[\w./-]+\.\w+)["\']'
    )
    for p in tests_dir.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        # This file itself documents the banned shapes in its
        # regex string + comments; exempt to avoid self-flagging.
        if p.name == Path(__file__).name:
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            m = pat.search(line)
            if m:
                bad_lines.append(f"{p.relative_to(REPO_ROOT)}:{i}: {m.group(0)}")
    assert not bad_lines, (
        "Tests reference real_/prod_/production_ path literals:\n  "
        + "\n  ".join(bad_lines)
    )


def test_append_only_audit_log_trigger_exists_in_migrations():
    """A7 + A4 — the migration that installs the
    `prevent_audit_mutation` trigger must remain in version control.
    Removing it would silently weaken the consent log's tamper-evidence
    guarantee."""
    migrations_dir = REPO_ROOT / "migrations" / "versions"
    found = False
    for p in migrations_dir.glob("*.py"):
        body = p.read_text(encoding="utf-8")
        if "prevent_audit_mutation" in body:
            found = True
            break
    assert found, (
        "No migration installs the `prevent_audit_mutation` trigger. "
        "The append-only audit-log invariant relies on it; restore the "
        "0016 migration or its successor."
    )
