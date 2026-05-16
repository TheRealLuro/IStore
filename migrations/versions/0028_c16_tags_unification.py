"""§C1.6 — tag unification (status → tags + per-user ownership + colors).

Single migration covering five changes that together unify the "user
labels something" surface:

1. **Per-user ownership.** `tags.user_id` becomes required (FK CASCADE
   on `users.id`). The legacy `unique(label)` constraint drops in
   favor of `unique(user_id, lower(label))` so two users can use the
   same "Important" label without colliding.
2. **Color.** `tags.color` (nullable 16-char string) — the FE picks
   the chip tint via the existing `data-tone` styling.
3. **Audit fields.** `created_at` / `updated_at` so the FE can show
   "tag last used" / sort by recency.
4. **Folder tags.** New `folder_tags` join table with the same
   RLS-friendly `user_id` column + FK CASCADE on folders + tags.
5. **Data backfill: `images.status` + `folders.status` → tags.**
   Every distinct (user_id, status, status_color) tuple becomes a
   tag row; every image/folder with a status gets the matching
   join row. The legacy `status` / `status_color` columns are
   PRESERVED for one release as a read-only shim so any client that
   hasn't switched yet still works; a follow-up migration can drop
   them once §C1.6 has shipped.

§A7 hygiene — RLS forced on the new `folder_tags` table the same way
0027 covered the other per-user-id tables.

Revision ID: 0028_c16_tags_unification
Revises: 0027_a4_rls_expand
Create Date: 2026-05-16 23:30:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0028_c16_tags_unification"
down_revision: Union[str, None] = "0027_a4_rls_expand"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_RLS_EXPR = (
    "current_setting('app.rls_bypass', true) = 'on' "
    "OR user_id::text = current_setting('app.current_user_id', true)"
)


def upgrade() -> None:
    # ---- 1. tags table: add user_id (nullable for now), color, ts ----
    op.add_column(
        "tags",
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tags",
        sa.Column("color", sa.String(16), nullable=True),
    )
    op.add_column(
        "tags",
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.add_column(
        "tags",
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # FK to users with CASCADE — deleting a user wipes their tags
    # together with everything else they own (§A5).
    op.create_foreign_key(
        "tags_user_id_fkey",
        "tags", "users",
        ["user_id"], ["id"],
        ondelete="CASCADE",
    )

    # ---- 2. backfill: any existing tag without a user_id gets bound
    #         to the first superuser so the row isn't orphaned. Tags
    #         created post-migration always carry the caller's user_id.
    #         If the deployment has no superusers, drop the rows — they
    #         can be regenerated on next semantic-search pass anyway.
    op.execute(
        """
        WITH first_super AS (
            SELECT id FROM users
            WHERE is_superuser IS TRUE
            ORDER BY id ASC LIMIT 1
        )
        UPDATE tags
        SET user_id = (SELECT id FROM first_super)
        WHERE user_id IS NULL;
        """
    )
    op.execute("DELETE FROM tags WHERE user_id IS NULL;")

    op.alter_column("tags", "user_id", nullable=False)

    # ---- 3. unique constraint: switch label → (user_id, lower(label)) ----
    # Drop the legacy single-column unique. The original migration
    # (0003_vision_columns) named it `tags_label_unique`, but installs
    # that pre-date that explicit name may have an auto-generated
    # `tags_label_key`. DROP IF EXISTS covers both shapes.
    op.execute("ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_label_unique")
    op.execute("ALTER TABLE tags DROP CONSTRAINT IF EXISTS tags_label_key")
    op.execute('DROP INDEX IF EXISTS "tags_label_key"')

    # Case-insensitive uniqueness per user via a functional index.
    op.execute(
        """
        CREATE UNIQUE INDEX tags_user_label_idx
        ON tags(user_id, lower(label))
        """
    )

    # ---- 4. image_tags + folder_tags: gain `user_id` denormalized for RLS ----
    op.add_column(
        "image_tags",
        sa.Column("user_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    # Backfill user_id from the joined image row.
    op.execute(
        """
        UPDATE image_tags it
        SET user_id = i.user_id
        FROM images i
        WHERE i.id = it.image_id AND it.user_id IS NULL;
        """
    )
    # Drop rows whose image has gone missing — should be empty given
    # the FK CASCADE, but defensive.
    op.execute("DELETE FROM image_tags WHERE user_id IS NULL;")
    op.alter_column("image_tags", "user_id", nullable=False)
    op.create_foreign_key(
        "image_tags_user_id_fkey",
        "image_tags", "users",
        ["user_id"], ["id"],
        ondelete="CASCADE",
    )

    # New folder_tags table.
    op.create_table(
        "folder_tags",
        sa.Column(
            "folder_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("folders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "tag_id",
            sa.BigInteger(),
            sa.ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "folder_tags_tag_idx", "folder_tags", ["tag_id", "folder_id"],
    )

    # ---- 5. RLS on image_tags + folder_tags ----
    # `image_tags` skipped in 0027 because it lacked a user_id; now it
    # has one, so cover it the same way as the other per-user tables.
    for table in ("image_tags", "folder_tags"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_user_isolation ON {table}")
        op.execute(
            f"""
            CREATE POLICY {table}_user_isolation ON {table}
            FOR ALL
            USING ({_RLS_EXPR})
            WITH CHECK ({_RLS_EXPR})
            """
        )
    # tags too — it's a user-scoped table now.
    op.execute("ALTER TABLE tags ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tags FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tags_user_isolation ON tags")
    op.execute(
        f"""
        CREATE POLICY tags_user_isolation ON tags
        FOR ALL
        USING ({_RLS_EXPR})
        WITH CHECK ({_RLS_EXPR})
        """
    )

    # ---- 6. data backfill: images.status → tags + image_tags ----
    # Distinct (user_id, status, status_color) tuples become tag rows.
    # We use `INSERT … ON CONFLICT DO NOTHING` against the new unique
    # index in case the same user already has a tag with that label.
    op.execute(
        """
        INSERT INTO tags (user_id, label, source, color, created_at, updated_at)
        SELECT DISTINCT user_id, status, 'user', status_color, now(), now()
        FROM images
        WHERE status IS NOT NULL AND status <> '' AND user_id IS NOT NULL
        ON CONFLICT DO NOTHING;
        """
    )
    # image_tags rows — match per (user_id, lower(label)).
    op.execute(
        """
        INSERT INTO image_tags (image_id, tag_id, user_id, confidence)
        SELECT i.id, t.id, i.user_id, NULL
        FROM images i
        JOIN tags t ON t.user_id = i.user_id
                     AND lower(t.label) = lower(i.status)
        WHERE i.status IS NOT NULL AND i.status <> ''
        ON CONFLICT DO NOTHING;
        """
    )

    # ---- 7. data backfill: folders.status → tags + folder_tags ----
    op.execute(
        """
        INSERT INTO tags (user_id, label, source, color, created_at, updated_at)
        SELECT DISTINCT user_id, status, 'user', status_color, now(), now()
        FROM folders
        WHERE status IS NOT NULL AND status <> '' AND user_id IS NOT NULL
        ON CONFLICT DO NOTHING;
        """
    )
    op.execute(
        """
        INSERT INTO folder_tags (folder_id, tag_id, user_id, created_at)
        SELECT f.id, t.id, f.user_id, now()
        FROM folders f
        JOIN tags t ON t.user_id = f.user_id
                     AND lower(t.label) = lower(f.status)
        WHERE f.status IS NOT NULL AND f.status <> ''
        ON CONFLICT DO NOTHING;
        """
    )


def downgrade() -> None:
    # Drop in reverse order.
    op.execute("DROP POLICY IF EXISTS folder_tags_user_isolation ON folder_tags")
    op.execute("ALTER TABLE folder_tags NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE folder_tags DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS image_tags_user_isolation ON image_tags")
    op.execute("ALTER TABLE image_tags NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE image_tags DISABLE ROW LEVEL SECURITY")

    op.execute("DROP POLICY IF EXISTS tags_user_isolation ON tags")
    op.execute("ALTER TABLE tags NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tags DISABLE ROW LEVEL SECURITY")

    op.drop_index("folder_tags_tag_idx", table_name="folder_tags")
    op.drop_table("folder_tags")

    op.drop_constraint("image_tags_user_id_fkey", "image_tags", type_="foreignkey")
    op.drop_column("image_tags", "user_id")

    op.execute('DROP INDEX IF EXISTS "tags_user_label_idx"')
    op.execute(
        'CREATE UNIQUE INDEX "tags_label_key" ON tags(label)'
    )

    op.drop_constraint("tags_user_id_fkey", "tags", type_="foreignkey")
    op.drop_column("tags", "updated_at")
    op.drop_column("tags", "created_at")
    op.drop_column("tags", "color")
    op.drop_column("tags", "user_id")
