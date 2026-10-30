"""Keep uncertainty separate for daily and game rewards; preserve user automation settings."""

import sqlalchemy as sa
from alembic import op

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.add_column(sa.Column('games_review_game', sa.String(128), nullable=True))
        batch.add_column(
            sa.Column('daily_needs_review', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column('games_needs_review', sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(
            sa.Column('daily_review_attempts', sa.Integer(), nullable=False, server_default='0')
        )
    with op.batch_alter_table('accounts') as batch:
        for name in ('daily_needs_review', 'games_needs_review', 'daily_review_attempts'):
            batch.alter_column(name, server_default=None)


def downgrade():
    # Downgrade must not remove the only guard against duplicate unknown claims.
    op.execute(
        sa.text(
            'UPDATE accounts SET needs_review = true, auto_collect = false '
            'WHERE daily_needs_review = true OR games_needs_review = true'
        )
    )
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('games_review_game')
        batch.drop_column('daily_review_attempts')
        batch.drop_column('games_needs_review')
        batch.drop_column('daily_needs_review')
