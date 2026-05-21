"""Regression test for D2 — image_persons IDOR write.

Threat model:

  POST /people/detect-and-label
    body.image_ids = [<victim_image_uuid>]
    body.display_name = "X"

  Pre-D2: the handler ran
    INSERT INTO image_persons (image_id, person_id, user_id, source)
      VALUES (<victim_image>, <attacker_person>, <attacker_user>, 'manual')
  BEFORE checking that any of those image_ids actually belonged to
  the caller. The FK only enforces that the image_id exists
  somewhere — not that the caller owns it.

  Reads stayed safe (list_images.user_id == caller filter), but the
  table's invariant "ImagePerson.image_id ⇒ caller owns the image"
  was broken. One future bug in a list/search path that trusted
  the invariant became an IDOR.

The fix in api.people.detect_and_label runs a `SELECT Image.id
WHERE id IN body.image_ids AND user_id = caller.id` BEFORE any
write, then re-binds body.image_ids to the owned subset. Foreign
ids are silently filtered (preserving the existing per-image
loop's UX) but they can never reach the ImagePerson bulk-insert.

Without a real DB the integration test path is heavy. These tests
source-shape the handler to pin:
  - the ownership-filtering SELECT happens BEFORE the
    `pg_insert(ImagePerson)` call
  - body.image_ids gets re-bound to the filtered set
  - the empty-after-filter branch returns early instead of
    allocating a Person row
"""
from __future__ import annotations

import inspect

from backend.api import people as people_mod


def test_ownership_filter_runs_before_bulk_insert() -> None:
    """The SELECT-for-ownership must appear in the source before
    the first pg_insert(ImagePerson). A regression that re-orders
    would silently re-open the IDOR write window."""
    src = inspect.getsource(people_mod.detect_and_label)

    select_pos = src.find("Image.user_id == user.id")
    insert_pos = src.find("pg_insert(ImagePerson)")
    assert select_pos > 0, (
        "detect_and_label is missing the `Image.user_id == user.id` "
        "ownership filter. D2 IDOR write is re-opened."
    )
    assert insert_pos > 0, "pg_insert(ImagePerson) call disappeared"
    assert select_pos < insert_pos, (
        f"Ownership SELECT must come BEFORE the ImagePerson bulk "
        f"insert. Current order: SELECT at {select_pos}, INSERT at "
        f"{insert_pos}."
    )


def test_body_image_ids_rebound_to_owned_set() -> None:
    """After the ownership filter, body.image_ids must be replaced
    with the filtered list so every DOWNSTREAM read of
    body.image_ids only sees authorized ids (the pre-existing
    Face fetch, the bulk INSERT, the per-image scan loop)."""
    src = inspect.getsource(people_mod.detect_and_label)
    assert "body.image_ids = " in src, (
        "body.image_ids is no longer re-bound to the owned subset. "
        "Downstream code paths may operate on attacker-supplied "
        "foreign ids again."
    )
    # And the rebound list must derive from `owned_image_ids` (the
    # name we use in the fix). A regression that pre-allocates a
    # different name would break this assertion — that's by design;
    # the test pins the structural shape.
    assert "owned_image_ids" in src, (
        "`owned_image_ids` set is missing. The structural shape of "
        "the fix has been refactored away."
    )


def test_empty_owned_set_short_circuits() -> None:
    """When the user submits only foreign / non-existent image_ids,
    the function must NOT create a Person row and run face scans.
    A regression that drops this early-return would expose the
    Person-creation side-channel (attacker probes by submitting
    bulk ids → counts created persons)."""
    src = inspect.getsource(people_mod.detect_and_label)
    # Look for the structural pattern: `if not body.image_ids:` +
    # early `return` BEFORE the Person query.
    early_check_pos = src.find("if not body.image_ids")
    person_query_pos = src.find("select(Person).where(")
    assert early_check_pos > 0, (
        "The empty-after-filter early-return is gone. An attack "
        "submitting only foreign image_ids would allocate a Person "
        "row anyway and expose a confirmation side-channel."
    )
    assert early_check_pos < person_query_pos, (
        "The empty-set early-return must run BEFORE the Person "
        "find-or-create. Current order makes the early-return "
        "useless."
    )
