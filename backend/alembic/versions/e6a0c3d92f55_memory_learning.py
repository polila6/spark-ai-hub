"""memories that the assistant learns by itself: a category, how important it is and how many times it came up

Existing memories keep working: they become category 'other', importance 3, seen once.

Revision ID: e6a0c3d92f55
Revises: c4e8a1f07d3b
Create Date: 2026-11-05 15:00:00.000000
"""
import sqlalchemy as sa
from alembic import op

revision = 'e6a0c3d92f55'
down_revision = 'c4e8a1f07d3b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('memories', sa.Column('category', sa.String(24), nullable=False, server_default='other'))
    op.add_column('memories', sa.Column('importance', sa.Integer(), nullable=False, server_default='3'))
    op.add_column('memories', sa.Column('mentions', sa.Integer(), nullable=False, server_default='1'))


def downgrade() -> None:
    op.drop_column('memories', 'mentions')
    op.drop_column('memories', 'importance')
    op.drop_column('memories', 'category')
