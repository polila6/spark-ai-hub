"""who accepted the terms that let an administrator read their content

NULL for everyone at first: earlier acceptances were of terms that said administrators do not read content, so they do not count.

Revision ID: c4e8a1f07d3b
Revises: a3c51e7d9b02
Create Date: 2026-11-04 18:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'c4e8a1f07d3b'
down_revision = 'a3c51e7d9b02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('content_access_accepted_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'content_access_accepted_at')
