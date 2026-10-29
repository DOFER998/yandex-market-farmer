"""Human-readable account names without changing existing identifiers or sessions."""

import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.add_column(
            sa.Column('display_name', sa.String(40), nullable=False, server_default='')
        )
    op.execute(sa.text('UPDATE accounts SET display_name = id'))
    with op.batch_alter_table('accounts') as batch:
        batch.alter_column('display_name', server_default=None)


def downgrade():
    with op.batch_alter_table('accounts') as batch:
        batch.drop_column('display_name')
