"""journal.items.withdrawn_by — источник последнего вывода КМ (гвард двойного вывода)

'' — не выводился; 'us' — наш LK_RECEIPT (withdraw_batch); 'wb' — WB вывел сам
по ККТ (гвард на отказ ЧЗ «код уже выбыл»). return_batch по нему выбирает
причину: REMOTE_SALE_RETURN ('us') vs RETAIL_RETURN ('wb').

Revision ID: 0008_item_withdrawn_by
Revises: 0007_wb_returns
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0008_item_withdrawn_by'
down_revision: Union[str, Sequence[str], None] = '0007_wb_returns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('items', sa.Column('withdrawn_by', sa.String(length=16),
                                     nullable=False, server_default=''),
                  schema='journal')


def downgrade() -> None:
    op.drop_column('items', 'withdrawn_by', schema='journal')
