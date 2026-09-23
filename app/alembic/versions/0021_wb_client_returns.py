"""wb.client_returns: стейджинг клиентских возвратов (sales R-строки)

После чек-сплита 01.09 (FBS) финансовый след — единственный детектор
возврата покупателя: op=2 эксайза не приходит, goods-return их не показывает.

Revision ID: 0021_wb_client_returns
Revises: 0020_events_km_index
Create Date: 2026-09-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSON

# revision identifiers, used by Alembic.
revision: str = '0021_wb_client_returns'
down_revision: Union[str, Sequence[str], None] = '0020_events_km_index'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'client_returns',
        sa.Column('srid', sa.String(96), primary_key=True),
        sa.Column('sale_id', sa.String(32), nullable=False, server_default=''),
        sa.Column('rdate', sa.String(32), nullable=False, server_default=''),
        sa.Column('warehouse', sa.String(64), nullable=False, server_default=''),
        sa.Column('order_doc', sa.String(64), nullable=False, server_default=''),
        sa.Column('km', sa.String(64), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('payload', JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        schema='wb')


def downgrade() -> None:
    op.drop_table('client_returns', schema='wb')
