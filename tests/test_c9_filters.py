"""§C9 — multi-axis image filtering acceptance.

Covers the two filter axes added in this pass to `GET /images/`:
  - `near=lat,lng,radius_km` — Haversine bounding-box on image_geo.
    Gated on `gps_retention` consent: 403 without it.
  - `taken_between=ISO,ISO` — date-range against COALESCE(taken_at,
    uploaded_at). Either side can be empty for an open-ended range.

Also covers the new facet payload fields:
  - `tags` — top tags + counts
  - `persons` — top persons + counts (only when face_recognition consent
    is active; otherwise an empty list)
  - `starred_count`, `date_range`
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from tests.conftest import (
    fetch_user_id,
    grant_consent,
    register_and_login,
)


async def _grant_gps(ac, headers):
    """Grant gps_retention via the generic /consent/{kind}/grant route."""
    r = await ac.post(
        "/consent/gps_retention/grant",
        json={"signature_text": "Test User"},
        headers=headers,
    )
    assert r.status_code == 201, r.text


async def _create_image(
    user_id,
    *,
    filename: str = "snap.jpg",
    uploaded_at: datetime | None = None,
    starred: bool = False,
    lat: float | None = None,
    lng: float | None = None,
    taken_at: datetime | None = None,
):
    from backend.db import SessionLocal
    from backend.models import Image, ImageGeo

    async with SessionLocal() as s:
        img = Image(
            user_id=user_id,
            category="image",
            original_filename=filename,
            served_blob_key=f"users/{user_id}/served/{uuid.uuid4().hex}.webp",
            original_blob_key=f"users/{user_id}/originals/{uuid.uuid4().hex}",
            pending_face_scan=False,
            pending_summary=False,
            byte_size_original=1024,
            byte_size_served=512,
            is_starred=starred,
        )
        if uploaded_at is not None:
            img.uploaded_at = uploaded_at
        s.add(img)
        await s.flush()
        if lat is not None and lng is not None:
            s.add(
                ImageGeo(
                    image_id=img.id,
                    user_id=user_id,
                    lat=lat,
                    lng=lng,
                    taken_at=taken_at,
                )
            )
        await s.commit()
        return img.id


# ---------- near= filter ----------


async def test_near_requires_gps_consent(db_client):
    _, headers = await register_and_login(db_client, email="c9-near-noconsent@example.com")
    r = await db_client.get("/images/?near=49.28,-123.12,10", headers=headers)
    assert r.status_code == 403
    assert "gps_retention" in r.json()["detail"]


async def test_near_rejects_bad_format(db_client):
    _, headers = await register_and_login(db_client, email="c9-near-badfmt@example.com")
    await _grant_gps(db_client, headers)
    # Two-part instead of three
    r = await db_client.get("/images/?near=49.28,-123.12", headers=headers)
    assert r.status_code == 400
    assert "three" in r.json()["detail"].lower() or "lat,lng,radius_km" in r.json()["detail"]
    # Non-numeric
    r = await db_client.get("/images/?near=abc,def,10", headers=headers)
    assert r.status_code == 400


async def test_near_rejects_zero_or_huge_radius(db_client):
    _, headers = await register_and_login(db_client, email="c9-near-radius@example.com")
    await _grant_gps(db_client, headers)
    r = await db_client.get("/images/?near=49.28,-123.12,0", headers=headers)
    assert r.status_code == 400
    r = await db_client.get("/images/?near=49.28,-123.12,40000", headers=headers)
    assert r.status_code == 400


async def test_near_returns_only_images_in_bbox(db_client):
    _, headers = await register_and_login(db_client, email="c9-near-bbox@example.com")
    await _grant_gps(db_client, headers)
    user_id = await fetch_user_id("c9-near-bbox@example.com")

    # Vancouver, BC: 49.28, -123.12.
    # Seattle, WA: 47.61, -122.33 — ~230 km south.
    # London, UK: 51.51, -0.13 — across the planet.
    near_van = await _create_image(user_id, filename="van.jpg", lat=49.28, lng=-123.12)
    near_sea = await _create_image(user_id, filename="sea.jpg", lat=47.61, lng=-122.33)
    far_lon = await _create_image(user_id, filename="lon.jpg", lat=51.51, lng=-0.13)

    # 50-km radius around Vancouver — Seattle is 230 km away so it
    # falls outside; London is half a planet away.
    r = await db_client.get(
        "/images/?near=49.28,-123.12,50&all=true", headers=headers
    )
    assert r.status_code == 200, r.text
    ids = {row["id"] for row in r.json()}
    assert str(near_van) in ids
    assert str(near_sea) not in ids
    assert str(far_lon) not in ids


# ---------- taken_between= filter ----------


async def test_taken_between_rejects_bad_format(db_client):
    _, headers = await register_and_login(db_client, email="c9-tb-badfmt@example.com")
    r = await db_client.get("/images/?taken_between=not-a-date", headers=headers)
    assert r.status_code == 400


async def test_taken_between_uses_uploaded_at_fallback(db_client):
    _, headers = await register_and_login(db_client, email="c9-tb-fallback@example.com")
    user_id = await fetch_user_id("c9-tb-fallback@example.com")

    jan = datetime(2026, 1, 15, tzinfo=timezone.utc)
    mar = datetime(2026, 3, 15, tzinfo=timezone.utc)
    may = datetime(2026, 5, 15, tzinfo=timezone.utc)
    jan_img = await _create_image(user_id, filename="jan.jpg", uploaded_at=jan)
    mar_img = await _create_image(user_id, filename="mar.jpg", uploaded_at=mar)
    may_img = await _create_image(user_id, filename="may.jpg", uploaded_at=may)

    # Both ends: Feb 1 – Apr 1 keeps only March.
    r = await db_client.get(
        "/images/?taken_between=2026-02-01,2026-04-01&all=true", headers=headers
    )
    assert r.status_code == 200
    ids = {row["id"] for row in r.json()}
    assert str(mar_img) in ids
    assert str(jan_img) not in ids
    assert str(may_img) not in ids

    # Open-ended start: ",2026-04-01" keeps Jan + Mar but not May.
    r = await db_client.get(
        "/images/?taken_between=,2026-04-01&all=true", headers=headers
    )
    ids = {row["id"] for row in r.json()}
    assert str(jan_img) in ids
    assert str(mar_img) in ids
    assert str(may_img) not in ids

    # Open-ended end: "2026-04-01," keeps only May.
    r = await db_client.get(
        "/images/?taken_between=2026-04-01,&all=true", headers=headers
    )
    ids = {row["id"] for row in r.json()}
    assert str(may_img) in ids
    assert str(jan_img) not in ids
    assert str(mar_img) not in ids


async def test_taken_between_prefers_geo_taken_at(db_client):
    """When image_geo.taken_at is present, the date filter uses that
    instead of uploaded_at. Uploaded a year ago, taken last month → the
    filter should see it as 'last month'."""
    _, headers = await register_and_login(db_client, email="c9-tb-geo@example.com")
    user_id = await fetch_user_id("c9-tb-geo@example.com")

    old_upload = datetime(2025, 6, 1, tzinfo=timezone.utc)
    fresh_capture = datetime(2026, 5, 1, tzinfo=timezone.utc)
    img_id = await _create_image(
        user_id,
        filename="hike.jpg",
        uploaded_at=old_upload,
        lat=49.28,
        lng=-123.12,
        taken_at=fresh_capture,
    )

    # April 2026 window — would EXCLUDE if we used uploaded_at (June 2025).
    r = await db_client.get(
        "/images/?taken_between=2026-04-01,2026-06-01&all=true", headers=headers
    )
    ids = {row["id"] for row in r.json()}
    assert str(img_id) in ids


# ---------- facets payload ----------


async def test_facets_includes_starred_and_date_range(db_client):
    _, headers = await register_and_login(db_client, email="c9-facets-basic@example.com")
    user_id = await fetch_user_id("c9-facets-basic@example.com")
    await _create_image(user_id, filename="a.jpg", uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc), starred=True)
    await _create_image(user_id, filename="b.jpg", uploaded_at=datetime(2026, 5, 1, tzinfo=timezone.utc))

    r = await db_client.get("/images/facets", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["starred_count"] == 1
    assert body["date_range"]["earliest"].startswith("2026-01-01")
    assert body["date_range"]["latest"].startswith("2026-05-01")
    # Keys present even when empty.
    assert isinstance(body["tags"], list)
    assert isinstance(body["persons"], list)


async def test_facets_persons_empty_without_face_consent(db_client):
    """Without face_recognition consent, /facets returns persons=[] even
    if Person rows exist for the user."""
    _, headers = await register_and_login(db_client, email="c9-facets-noface@example.com")
    user_id = await fetch_user_id("c9-facets-noface@example.com")
    # Create a person row directly — without consent the facet should
    # still hide them.
    from backend.db import SessionLocal
    from backend.models import Person

    async with SessionLocal() as s:
        s.add(Person(user_id=user_id, display_name="Alice"))
        await s.commit()

    r = await db_client.get("/images/facets", headers=headers)
    assert r.status_code == 200
    assert r.json()["persons"] == []


async def test_facets_tags_lists_user_tags(db_client):
    _, headers = await register_and_login(db_client, email="c9-facets-tags@example.com")
    user_id = await fetch_user_id("c9-facets-tags@example.com")
    img_id = await _create_image(user_id)

    # Attach a tag via the public endpoint so we exercise the same
    # path the FE uses.
    r = await db_client.post(
        f"/images/{img_id}/tags",
        json={"label": "vacation"},
        headers=headers,
    )
    assert r.status_code in (200, 201), r.text

    r = await db_client.get("/images/facets", headers=headers)
    assert r.status_code == 200
    tags = r.json()["tags"]
    assert any(t["label"] == "vacation" and t["count"] == 1 for t in tags)
