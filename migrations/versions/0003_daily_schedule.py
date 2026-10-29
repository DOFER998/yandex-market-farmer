"""Persist Market daily reward deadline separately from the planned run."""

import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.add_column(sa.Column('daily_reward_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('daily_available', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('daily_available')
        batch.drop_column('daily_reward_at')
