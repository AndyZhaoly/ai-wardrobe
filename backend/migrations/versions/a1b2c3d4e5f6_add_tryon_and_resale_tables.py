"""add tryon and resale tables

Revision ID: a1b2c3d4e5f6
Revises: df5d193d2b23
Create Date: 2026-05-05 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "df5d193d2b23"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── Extend clothing_items ─────────────────────────────────────────────────
    op.add_column("clothing_items", sa.Column("mask_image_path", sa.String(), nullable=True))
    op.add_column("clothing_items", sa.Column("is_demo", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("clothing_items", sa.Column("source", sa.String(), server_default="upload", nullable=False))
    # source values: 'upload' | 'demo' | 'tryon'

    # ── tryon_sessions ────────────────────────────────────────────────────────
    op.create_table(
        "tryon_sessions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("person_image_path", sa.String(), nullable=False),
        sa.Column("garment_item_id", sa.UUID(), nullable=True),
        sa.Column("result_image_path", sa.String(), nullable=True),
        sa.Column("clothing_category", sa.String(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["garment_item_id"], ["clothing_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tryon_sessions_user_id", "tryon_sessions", ["user_id"])
    op.create_index("ix_tryon_sessions_status", "tryon_sessions", ["status"])

    # ── resale_listings ───────────────────────────────────────────────────────
    op.create_table(
        "resale_listings",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("garment_image_path", sa.String(), nullable=False),
        sa.Column("cropped_image_path", sa.String(), nullable=True),
        sa.Column("listing_title", sa.String(50), nullable=True),
        sa.Column("listing_description", sa.Text(), nullable=True),
        sa.Column("original_price_cny", sa.Integer(), nullable=True),
        sa.Column("listing_price_usd", sa.Integer(), nullable=True),
        sa.Column("poshmark_category_path", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(), server_default="draft", nullable=False),
        # status values: 'draft' | 'posted' | 'error'
        sa.Column("poshmark_listing_id", sa.String(), nullable=True),
        sa.Column("posted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["clothing_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resale_listings_user_id", "resale_listings", ["user_id"])
    op.create_index("ix_resale_listings_status", "resale_listings", ["status"])


def downgrade() -> None:
    op.drop_table("resale_listings")
    op.drop_table("tryon_sessions")
    op.drop_column("clothing_items", "source")
    op.drop_column("clothing_items", "is_demo")
    op.drop_column("clothing_items", "mask_image_path")
