"""wb.orders.order_id: числовой ID сборочного задания для WB orders/meta

Revision ID: 0018_wb_orders_order_id
Revises: 0017_producers_decl_rich
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0018_wb_orders_order_id'
down_revision: Union[str, Sequence[str], None] = '0017_producers_decl_rich'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # снапшот /api/v3/orders отдаёт id сборочного задания; трассировка КМ
    # запрашивает статусы закрепления sgtin именно по числовым ID
    op.add_column('orders', sa.Column('order_id', sa.BigInteger(), nullable=True),
                  schema='wb')


def downgrade() -> None:
    op.drop_column('orders', 'order_id', schema='wb')
