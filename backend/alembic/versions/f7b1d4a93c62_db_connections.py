"""remote database connections: queried live and read-only, never downloaded

Revision ID: f7b1d4a93c62
Revises: e6a0c3d92f55
Create Date: 2026-11-05 16:00:00.000000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = 'f7b1d4a93c62'
down_revision = 'e6a0c3d92f55'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'db_connections',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('owner_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(80), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False, server_default='postgres'),
        sa.Column('host', sa.String(255), nullable=False),
        sa.Column('port', sa.Integer(), nullable=False, server_default='5432'),
        sa.Column('database', sa.String(128), nullable=False),
        sa.Column('username', sa.String(128), nullable=False),
        sa.Column('secret', sa.Text(), nullable=True),
        sa.Column('sslmode', sa.String(16), nullable=False, server_default='require'),
        sa.Column('row_limit', sa.Integer(), nullable=False, server_default='500'),
        sa.Column('timeout_s', sa.Integer(), nullable=False, server_default='20'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('owner_id', 'name', name='uq_db_connections_owner_name'),
    )
    op.create_index('ix_db_connections_owner_id', 'db_connections', ['owner_id'])


def downgrade() -> None:
    op.drop_index('ix_db_connections_owner_id', table_name='db_connections')
    op.drop_table('db_connections')
