"""journal.items: колонки проверки КИЗ в ЧЗ (cis_status/cis_product_name/cis_checked_at)

Revision ID: 0014_cis_status
Revises: 0013_console_auth
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0014_cis_status'
down_revision: Union[str, Sequence[str], None] = '0013_console_auth'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('items',
                  sa.Column('cis_status', sa.String(length=32), nullable=False,
                            server_default=''), schema='journal')
    op.add_column('items',
                  sa.Column('cis_product_name', sa.String(length=256), nullable=False,
                            server_default=''), schema='journal')
    op.add_column('items',
                  sa.Column('cis_checked_at', sa.DateTime(), nullable=True),
                  schema='journal')


def downgrade() -> None:
    op.drop_column('items', 'cis_checked_at', schema='journal')
    op.drop_column('items', 'cis_product_name', schema='journal')
    op.drop_column('items', 'cis_status', schema='journal')
