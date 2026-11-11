"""turn checkpoints: progress of long turns saved every few steps, to resume after a restart

Revision ID: a8c2e5d7b913
Revises: f7b1d4a93c62
Create Date: 2026-11-11 12:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'a8c2e5d7b913'
down_revision = 'f7b1d4a93c62'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'turn_checkpoints',
        sa.Column('conversation_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('conversations.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_message_id', sa.BigInteger(), nullable=True),
        sa.Column('model_alias', sa.String(128), nullable=True),
        sa.Column('step', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('payload', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_turn_checkpoints_user_id', 'turn_checkpoints', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_turn_checkpoints_user_id', table_name='turn_checkpoints')
    op.drop_table('turn_checkpoints')
