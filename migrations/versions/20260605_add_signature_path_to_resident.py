"""Add signature_path column to Resident

Revision ID: 20260605_add_signature_path
Revises: None
Create Date: 2026-06-05 01:42:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "20260605_add_signature_path"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("residents", sa.Column("signature_path", sa.String(255), nullable=True))


def downgrade():
    op.drop_column("residents", "signature_path")
