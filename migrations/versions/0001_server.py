"""Accounts, durable run/operation journal and balance history."""

import sqlalchemy as sa
from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'accounts',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('auth_state', sa.String(24), nullable=False),
        sa.Column('auto_collect', sa.Boolean(), nullable=False),
        sa.Column('notifications', sa.Boolean(), nullable=False),
        sa.Column('interval_minutes', sa.Integer(), nullable=False),
        sa.Column('next_run_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('needs_review', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('interval_minutes BETWEEN 15 AND 1440', name='account_interval'),
    )
    op.create_index('ix_accounts_next_run_at', 'accounts', ['next_run_at'])
    op.create_table(
        'farm_runs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(32), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('trigger', sa.String(24), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('balance_before', sa.Integer()),
        sa.Column('balance_after', sa.Integer()),
        sa.Column('error_code', sa.String(80)),
    )
    op.create_index('ix_farm_runs_account_id', 'farm_runs', ['account_id'])
    op.create_index('ix_farm_runs_started_at', 'farm_runs', ['started_at'])
    op.create_table(
        'operations',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('farm_runs.id'), nullable=False),
        sa.Column('account_id', sa.String(32), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('kind', sa.String(80), nullable=False),
        sa.Column('state', sa.String(40), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('error_code', sa.String(80)),
    )
    for field in ('run_id', 'account_id', 'created_at'):
        op.create_index('ix_operations_' + field, 'operations', [field])
    op.create_table(
        'balance_snapshots',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(32), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('run_id', sa.String(36), sa.ForeignKey('farm_runs.id'), nullable=False),
        sa.Column('coins', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('coins >= 0', name='balance_nonnegative'),
    )
    for field in ('account_id', 'created_at'):
        op.create_index('ix_balance_snapshots_' + field, 'balance_snapshots', [field])


def downgrade():
    for table in ('balance_snapshots', 'operations', 'farm_runs', 'accounts'):
        op.drop_table(table)
