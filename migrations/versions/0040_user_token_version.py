"""Audit A8 — session-JWT revocation handle.

Adds `users.token_version` (BIGINT NOT NULL DEFAULT 1).

Every JWT minted for a user embeds the current value as a `tv`
claim. The decode path compares the claim against this column and
rejects tokens whose value doesn't match — bumping the column
invalidates every live session for that user.

Bumped automatically on:
  - password reset      (backend.auth.users.UserManager.on_after_reset_password)
  - 2FA disable         (backend.api.two_factor.two_factor_disable)

Default 1 so existing rows validate cleanly against tokens minted
during the deploy window. The decode path also treats a missing
`tv` claim as `tv=1`, so JWTs minted by the pre-A8 build still
authenticate until they expire — no forced sign-out at deploy.

Why BIGINT not INTEGER: every bump is one INSERT-vs-update cost,
but the column is hot in every authenticated request. BIGINT's
8 bytes vs INTEGER's 4 is irrelevant against the 24-byte UUID PK
+ row header; not worth distinguishing.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0040_user_token_version"
down_revision: Union[str, None] = "0039_cloud_exclusion_tracking"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.BigInteger(),
            nullable=False,
            server_default="1",
            comment=(
                "JWT-revocation handle (A8). Bumped on password reset / "
                "2FA disable. Minted into the `tv` claim; decode path "
                "rejects mismatched tokens."
            ),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
