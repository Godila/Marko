"""wb.orders.supply_id: поставка заказа для этапа «отгрузка/приёмка на складе WB»

Revision ID: 0019_wb_orders_supply
Revises: 0018_wb_orders_order_id
Create Date: 2026-09-22
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0019_wb_orders_supply'
down_revision: Union[str, Sequence[str], None] = '0018_wb_orders_order_id'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # снапшот /api/v3/orders отдаёт supplyId; даты самой поставки (создана/
    # закрыта/просканирована на складе) воркер кладёт в kv wb_supplies
    op.add_column('orders', sa.Column('supply_id', sa.String(32), nullable=True),
                  schema='wb')


def downgrade() -> None:
    op.drop_column('orders', 'supply_id', schema='wb')
