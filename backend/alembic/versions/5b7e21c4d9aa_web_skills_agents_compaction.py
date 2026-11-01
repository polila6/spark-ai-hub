"""chat: skills, agent profiles, conversation compaction

Revision ID: 5b7e21c4d9aa
Revises: cda914892726
Create Date: 2026-10-30 15:30:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '5b7e21c4d9aa'
down_revision = 'cda914892726'
branch_labels = None
depends_on = None

VIS = postgresql.ENUM('private', 'shared', 'public', name='visibility', create_type=False)


def upgrade() -> None:
    op.add_column('conversations', sa.Column('summary', sa.Text(), nullable=True))
    op.add_column('conversations', sa.Column('summary_upto', sa.BigInteger(), nullable=True))
    op.add_column('conversations', sa.Column('summary_meta', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_table('skills',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('files', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source_url', sa.String(length=1024), nullable=True),
    sa.Column('license', sa.String(length=255), nullable=True),
    sa.Column('installed_by', sa.String(length=16), nullable=False),
    sa.Column('visibility', VIS, nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('use_count', sa.Integer(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name='fk_skills_owner_id_users', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'name', name='uq_skills_owner_name')
    )
    op.create_index(op.f('ix_skills_owner_id'), 'skills', ['owner_id'], unique=False)
    op.create_table('agent_profiles',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('tools', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('skills', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('visibility', VIS, nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], name='fk_agent_profiles_owner_id_users', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('owner_id', 'name', name='uq_agent_profiles_owner_name')
    )
    op.create_index(op.f('ix_agent_profiles_owner_id'), 'agent_profiles', ['owner_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_agent_profiles_owner_id'), table_name='agent_profiles')
    op.drop_table('agent_profiles')
    op.drop_index(op.f('ix_skills_owner_id'), table_name='skills')
    op.drop_table('skills')
    op.drop_column('conversations', 'summary_meta')
    op.drop_column('conversations', 'summary_upto')
    op.drop_column('conversations', 'summary')
