"""VLT-2 — broadened file-type support in detect_magic.

Unknown-but-safe binaries should ingest as opaque ("other") files
instead of being rejected, while the dangerous-content blocks
(HTML / SVG / script) must STILL reject regardless of extension.
"""

import pytest

from backend.upload_validation import (
    UploadValidationError,
    detect_magic,
    _client_mime_ok,
)


def test_unknown_binary_accepted_as_other():
    # A .aep / .sketch-style opaque binary (random non-text bytes,
    # no recognized magic) should now be accepted as octet-stream.
    blob = bytes([0x00, 0x01, 0x02, 0xFF, 0xFE, 0x10, 0x42]) * 50
    mime, category = detect_magic(blob, "Paint Logo Reveal.aep")
    assert category == "other"
    assert mime == "application/octet-stream"


def test_generic_zip_accepted_as_other():
    # PK-magic zip with a non-OOXML extension → opaque, not rejected.
    zip_blob = b"PK\x03\x04" + bytes(200)
    mime, category = detect_magic(zip_blob, "videohive-template.zip")
    assert category == "other"
    assert mime == "application/octet-stream"


def test_ooxml_still_routes_to_document():
    # A .docx is also PK-magic but must stay a document, not "other".
    docx_blob = b"PK\x03\x04" + bytes(200)
    mime, category = detect_magic(docx_blob, "report.docx")
    assert category == "document"
    assert "wordprocessingml" in mime


def test_html_still_rejected_regardless_of_extension():
    # HTML content under a .xyz extension must STILL be rejected — the
    # block is content-based, not extension-based. This is the XSS guard.
    html = b"<!doctype html><html><body><script>alert(1)</script></html>"
    with pytest.raises(UploadValidationError):
        detect_magic(html, "innocent.xyz")


def test_svg_still_rejected():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>'
    with pytest.raises(UploadValidationError):
        detect_magic(svg, "logo.bin")


def test_scriptable_text_under_weird_ext_rejected():
    # A text-shaped file with a script tag under an unknown extension
    # should be rejected by the _reject_scriptable_text guard rather
    # than accepted as text/plain "other".
    payload = b"hello\n<script>steal()</script>\nworld\n"
    with pytest.raises(UploadValidationError):
        detect_magic(payload, "notes.weirdext")


def test_plain_text_under_weird_ext_accepted_as_document():
    # ANY UTF-8-clean text file is now made VIEWABLE rather than
    # download-only: an unrecognized-but-textual extension earns a
    # viewable `text/x-<token>` MIME (derived from the extension) so the
    # FE renders it through the code preview, with a graceful
    # plain-monospace fallback when there's no Prism grammar. A clean
    # identifier extension like `weirdext` yields `text/x-weirdext`.
    payload = b"just some plain notes\nwith multiple lines\n" * 10
    mime, category = detect_magic(payload, "notes.weirdext")
    assert category == "document"
    assert mime == "text/x-weirdext"
    # The key invariant either way: it's a viewable text MIME, never the
    # download-only octet-stream.
    assert mime.startswith("text/")


def test_no_extension_text_still_plain_text():
    # A text file with NO usable extension still falls back to the
    # generic viewable `text/plain` (no language token to derive).
    payload = b"plain notes, no extension at all\n" * 10
    mime, category = detect_magic(payload, "NOTES")
    assert category == "document"
    assert mime == "text/plain"


def test_client_mime_mismatch_ignored_for_opaque():
    # Provider reports a vendor MIME but we detect octet-stream — the
    # mismatch must NOT cause a rejection for opaque files.
    assert _client_mime_ok("application/vnd.jgraph.mxfile", "application/octet-stream") is True


def test_client_mime_mismatch_still_enforced_for_known_types():
    # The relaxation is ONLY for octet-stream. A claimed PDF that
    # detected as something else must still be caught.
    assert _client_mime_ok("application/pdf", "image/png") is False
