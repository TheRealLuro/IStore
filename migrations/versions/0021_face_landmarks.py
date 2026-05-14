"""face_detections.landmarks_json column

Persists the 5-point RetinaFace key points (left eye, right eye, nose,
mouth-left, mouth-right) that the buffalo_l model already returns. The
prior detection pass discarded them. Persisting them lets the People
avatar regenerator re-crop existing detections with eye-line-centered
framing without re-running detection.

Revision ID: 0021_face_landmarks
Revises: 0020_image_geo_place
Create Date: 2026-05-14 22:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0021_face_landmarks"
down_revision: Union[str, None] = "0020_image_geo_place"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "face_detections",
        sa.Column("landmarks_json", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("face_detections", "landmarks_json")
