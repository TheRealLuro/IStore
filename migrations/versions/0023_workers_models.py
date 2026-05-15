"""worker_heartbeats + model_runs tables (todo C8.2)

Two C8.2 deliverables:

  worker_heartbeats — per-worker liveness so the API can list
  every container in a split deploy (psutil only sees processes in
  the same OS namespace). Each worker upserts every 30 s. The
  admin Processes tab unions psutil rows with these heartbeats.

  model_runs — ml-worker reports model load/unload events here so
  /admin/models can show real device + memory state. Rows accumulate;
  the latest per (worker_id, model_id) wins. The table doubles as a
  trail of "what was loaded when" for incident response.

Revision ID: 0023_workers_models
Revises: 0022_share_grants
Create Date: 2026-05-15 00:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0023_workers_models"
down_revision: Union[str, None] = "0022_share_grants"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column(
            "last_seen",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("version", sa.String(32), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
    )
    op.create_index(
        "worker_heartbeats_last_seen_idx",
        "worker_heartbeats",
        ["last_seen"],
    )

    op.create_table(
        "model_runs",
        sa.Column(
            "id",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("model_id", sa.String(64), nullable=False),
        sa.Column("worker_id", sa.Text(), nullable=False),
        # 'loading' | 'loaded' | 'unloaded' | 'error'
        sa.Column("state", sa.String(16), nullable=False),
        # 'cuda:0' | 'cpu' | 'xpu' | 'npu' | 'mps' | ''
        sa.Column("device", sa.String(16), nullable=True),
        sa.Column("memory_allocated_bytes", sa.BigInteger(), nullable=True),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("metadata", JSONB(), nullable=True),
    )
    op.create_index(
        "model_runs_model_worker_idx",
        "model_runs",
        ["model_id", "worker_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("model_runs_model_worker_idx", table_name="model_runs")
    op.drop_table("model_runs")
    op.drop_index("worker_heartbeats_last_seen_idx", table_name="worker_heartbeats")
    op.drop_table("worker_heartbeats")
