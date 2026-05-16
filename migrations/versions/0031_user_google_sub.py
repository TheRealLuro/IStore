"""Add users.google_sub so the SSO and Drive flows can share an identity.

When an already-signed-in user connects Drive, the OAuth flow now
captures their Google `sub` claim and stamps it onto their User row.
Future SSO sign-ins (`/auth/google/callback`) match against this column
before falling back to email — so the user who connected Drive ends up
signed back in to *the same* neuthek account on the next Google
sign-in, not a fresh one created from the email lookup.

Revision ID: 0031_user_google_sub
Revises: 0030_cloud_ai_opted_in
"""
from alembic import op
import sqlalchemy as sa


revision = "0031_user_google_sub"
down_revision = "0030_cloud_ai_opted_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("google_sub", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_users_google_sub",
        "users",
        ["google_sub"],
        unique=True,
        postgresql_where=sa.text("google_sub IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_users_google_sub", table_name="users")
    op.drop_column("users", "google_sub")
