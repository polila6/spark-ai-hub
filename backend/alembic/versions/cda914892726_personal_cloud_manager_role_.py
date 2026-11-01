"""personal cloud, manager role, notifications

Revision ID: cda914892726
Revises: 1a0f816addd6
Create Date: 2026-10-30 10:07:05.691769
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'cda914892726'
down_revision = '1a0f816addd6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'manager' AFTER 'admin'")
    op.execute("ALTER TYPE download_status ADD VALUE IF NOT EXISTS 'rejected'")
    op.create_table('email_outbox',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('to_addr', sa.String(length=255), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('body_text', sa.Text(), nullable=False),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.Text(), nullable=True),
    sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_email_outbox_status'), 'email_outbox', ['status'], unique=False)
    op.create_table('kb_collections',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('visibility', postgresql.ENUM('private', 'shared', 'public', name='visibility', create_type=False), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_kb_collections_owner_id'), 'kb_collections', ['owner_id'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('link', sa.String(length=512), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_table('projects',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('instructions', sa.Text(), nullable=False),
    sa.Column('collection_id', sa.UUID(), nullable=True),
    sa.Column('default_model', sa.String(length=128), nullable=True),
    sa.Column('visibility', postgresql.ENUM('private', 'shared', 'public', name='visibility', create_type=False), nullable=False),
    sa.Column('archived', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['kb_collections.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_owner_id'), 'projects', ['owner_id'], unique=False)
    op.create_table('user_files',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('project_id', sa.UUID(), nullable=True),
    sa.Column('folder', sa.String(length=255), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('mime', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('path', sa.Text(), nullable=False),
    sa.Column('text_status', sa.String(length=16), nullable=False),
    sa.Column('text_chars', sa.Integer(), nullable=False),
    sa.Column('meta', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['project_id'], ['projects.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_user_files_owner_id'), 'user_files', ['owner_id'], unique=False)
    op.create_index(op.f('ix_user_files_project_id'), 'user_files', ['project_id'], unique=False)
    op.create_table('kb_documents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('collection_id', sa.UUID(), nullable=False),
    sa.Column('file_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('chunks', sa.Integer(), nullable=False),
    sa.Column('embedding_model', sa.String(length=128), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('job_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['kb_collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['file_id'], ['user_files.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('collection_id', 'file_id', name='uq_kb_doc')
    )
    op.create_index(op.f('ix_kb_documents_collection_id'), 'kb_documents', ['collection_id'], unique=False)
    op.create_index(op.f('ix_kb_documents_file_id'), 'kb_documents', ['file_id'], unique=False)
    op.create_index(op.f('ix_kb_documents_owner_id'), 'kb_documents', ['owner_id'], unique=False)
    op.create_table('memories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memories_owner_id'), 'memories', ['owner_id'], unique=False)
    op.create_table('kb_chunks',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('collection_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('idx', sa.Integer(), nullable=False),
    sa.Column('page', sa.Integer(), nullable=True),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=128), nullable=False),
    sa.ForeignKeyConstraint(['collection_id'], ['kb_collections.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['document_id'], ['kb_documents.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_kb_chunks_collection_id'), 'kb_chunks', ['collection_id'], unique=False)
    op.create_index(op.f('ix_kb_chunks_document_id'), 'kb_chunks', ['document_id'], unique=False)
    op.create_index(op.f('ix_kb_chunks_owner_id'), 'kb_chunks', ['owner_id'], unique=False)
    op.add_column('conversations', sa.Column('project_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_conversations_project_id'), 'conversations', ['project_id'], unique=False)
    op.create_foreign_key('fk_conversations_project', 'conversations', 'projects', ['project_id'], ['id'], ondelete='SET NULL')
    op.add_column('download_tasks', sa.Column('request_reason', sa.Text(), nullable=True))
    op.add_column('download_tasks', sa.Column('reviewed_by', sa.UUID(), nullable=True))
    op.add_column('download_tasks', sa.Column('reviewed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('download_tasks', sa.Column('review_comment', sa.Text(), nullable=True))
    op.create_foreign_key('fk_download_tasks_reviewed_by', 'download_tasks', 'users', ['reviewed_by'], ['id'], ondelete='SET NULL')
    op.add_column('messages', sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('messages', sa.Column('parts', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('users', sa.Column('signup_status', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('signup_reason', sa.Text(), nullable=True))
    op.add_column('users', sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('terms_accepted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('users', sa.Column('terms_version', sa.Integer(), nullable=True))
    op.add_column('users', sa.Column('invited_by', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_users_signup_status'), 'users', ['signup_status'], unique=False)
    op.create_foreign_key('fk_users_invited_by', 'users', 'users', ['invited_by'], ['id'], ondelete='SET NULL')
    # pgvector embedding (dimension depends on the configured embedding model, so the column is untyped and searches
    # filter by `model`) and a generated full-text column for hybrid retrieval.
    op.execute("ALTER TABLE kb_chunks ADD COLUMN embedding vector")
    op.execute("ALTER TABLE kb_chunks ADD COLUMN tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', text)) STORED")
    op.execute("CREATE INDEX ix_kb_chunks_tsv ON kb_chunks USING gin (tsv)")
    op.execute("CREATE INDEX ix_messages_fts ON messages USING gin (to_tsvector('simple', content))")
    op.execute("CREATE INDEX ix_conversations_title_fts ON conversations USING gin (to_tsvector('simple', title))")


def downgrade() -> None:
    # Enum values added in upgrade() cannot be removed in PostgreSQL; they are harmless if unused.
    op.execute("DROP INDEX IF EXISTS ix_conversations_title_fts")
    op.execute("DROP INDEX IF EXISTS ix_messages_fts")
    op.drop_constraint('fk_users_invited_by', 'users', type_='foreignkey')
    op.drop_index(op.f('ix_users_signup_status'), table_name='users')
    op.drop_column('users', 'invited_by')
    op.drop_column('users', 'terms_version')
    op.drop_column('users', 'terms_accepted_at')
    op.drop_column('users', 'email_verified_at')
    op.drop_column('users', 'signup_reason')
    op.drop_column('users', 'signup_status')
    op.drop_column('messages', 'parts')
    op.drop_column('messages', 'attachments')
    op.drop_constraint('fk_download_tasks_reviewed_by', 'download_tasks', type_='foreignkey')
    op.drop_column('download_tasks', 'review_comment')
    op.drop_column('download_tasks', 'reviewed_at')
    op.drop_column('download_tasks', 'reviewed_by')
    op.drop_column('download_tasks', 'request_reason')
    op.drop_constraint('fk_conversations_project', 'conversations', type_='foreignkey')
    op.drop_index(op.f('ix_conversations_project_id'), table_name='conversations')
    op.drop_column('conversations', 'project_id')
    op.drop_index(op.f('ix_kb_chunks_owner_id'), table_name='kb_chunks')
    op.drop_index(op.f('ix_kb_chunks_document_id'), table_name='kb_chunks')
    op.drop_index(op.f('ix_kb_chunks_collection_id'), table_name='kb_chunks')
    op.drop_table('kb_chunks')
    op.drop_index(op.f('ix_memories_owner_id'), table_name='memories')
    op.drop_table('memories')
    op.drop_index(op.f('ix_kb_documents_owner_id'), table_name='kb_documents')
    op.drop_index(op.f('ix_kb_documents_file_id'), table_name='kb_documents')
    op.drop_index(op.f('ix_kb_documents_collection_id'), table_name='kb_documents')
    op.drop_table('kb_documents')
    op.drop_index(op.f('ix_user_files_project_id'), table_name='user_files')
    op.drop_index(op.f('ix_user_files_owner_id'), table_name='user_files')
    op.drop_table('user_files')
    op.drop_index(op.f('ix_projects_owner_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_kb_collections_owner_id'), table_name='kb_collections')
    op.drop_table('kb_collections')
    op.drop_index(op.f('ix_email_outbox_status'), table_name='email_outbox')
    op.drop_table('email_outbox')
    # ### end Alembic commands ###
