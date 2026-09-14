"""wb.orders — реестр заказов WB для классификации FBS/FBW excise-строк

Revision ID: 0012_wb_orders
Revises: 0011_rule_multitype
Create Date: 2026-09-14

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0012_wb_orders'
down_revision: Union[str, Sequence[str], None] = '0011_rule_multitype'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS wb')
    op.create_table('orders',
    sa.Column('order_doc', sa.String(length=64), nullable=False),
    sa.Column('delivery_type', sa.String(length=8), nullable=False, server_default=''),
    sa.Column('nm_id', sa.BigInteger(), nullable=True),
    sa.Column('order_created_at', sa.String(length=32), nullable=False, server_default=''),
    sa.Column('first_seen', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('last_seen', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('order_doc'),
    schema='wb'
    )


def downgrade() -> None:
    op.drop_table('orders', schema='wb')
