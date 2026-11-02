"""share skills and agents with everyone (data only)

Skills and custom agents are now shared with every user by default. Existing private ones become public;
their owner (or an admin/manager) can make one private again from the Skills / Agents pages.

Revision ID: a3c51e7d9b02
Revises: 38ed2f96b523
Create Date: 2026-11-02 10:00:00.000000
"""
from alembic import op

revision = 'a3c51e7d9b02'
down_revision = '38ed2f96b523'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE skills SET visibility = 'public' WHERE visibility <> 'public'")
    op.execute("UPDATE agent_profiles SET visibility = 'public' WHERE visibility <> 'public'")


def downgrade() -> None:
    # Data only: which rows were private before cannot be known, so nothing is reverted.
    pass
