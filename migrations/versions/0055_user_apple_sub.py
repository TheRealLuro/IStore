"""Sign in with Apple — users.apple_sub (Apple SSO subject identifier).

The mirror of `users.google_sub` for "Sign in with Apple". Apple's OpenID
Connect `sub` claim is the stable, per-Services-ID identifier for a user;
we store it so `/auth/apple/callback` can resolve "which neuthek user is
this Apple account?" with one indexed lookup — exactly the way `google_sub`
backs Google Sign-In.

Column shape:
  * nullable — the vast majority of rows have no Apple identity;
  * UNIQUE — a given Apple account maps to at most one neuthek user, so a
    second user can never claim someone else's Apple `sub`;
  * indexed — the callback's primary lookup is by `apple_sub`.

Apple `sub` values are short (~44 chars in practice) but the spec allows up
to 255, so we size generously rather than risk a truncated identifier
silently failing the unique lookup.

Reversible: drops the index + column.

Revision ID: 0055_user_apple_sub
Revises: 0054_sftp_access
Create Date: 2026-06-01 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0055_user_apple_sub"
down_revision: Union[str, None] = "0054_sftp_access"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("apple_sub", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_users_apple_sub", "users", ["apple_sub"], unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_users_apple_sub", table_name="users")
    op.drop_column("users", "apple_sub")
