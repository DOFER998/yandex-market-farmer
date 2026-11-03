"""Persist per-source preferences, status, retry deadlines and notification deduplication."""

import sqlalchemy as sa
from alembic import op

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.add_column(
            sa.Column('games_auto', sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(
            sa.Column('live_auto', sa.Boolean(), nullable=False, server_default=sa.true())
        )
    op.create_table(
        'bonus_status',
        sa.Column('account_id', sa.String(32), sa.ForeignKey('accounts.id'), primary_key=True),
        sa.Column('source', sa.String(16), primary_key=True),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('next_check_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('balance', sa.Integer(), nullable=True),
        sa.Column('limit', sa.Integer(), nullable=True),
        sa.Column('remaining_seconds', sa.Integer(), nullable=True),
        sa.Column('error_code', sa.String(80), nullable=True),
        sa.Column('cycle', sa.Integer(), nullable=False),
        sa.Column('notified_key', sa.String(160), nullable=True),
    )
    with op.batch_alter_table('accounts') as batch:
        batch.alter_column('games_auto', server_default=None)
        batch.alter_column('live_auto', server_default=None)


def downgrade():
    op.drop_table('bonus_status')
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('live_auto')
        batch.drop_column('games_auto')
