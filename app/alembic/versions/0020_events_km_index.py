"""journal.events (km, kind): bulk-вехи журнала и трассировка без seq scan

Revision ID: 0020_events_km_index
Revises: 0019_wb_orders_supply
Create Date: 2026-09-22
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0020_events_km_index'
down_revision: Union[str, Sequence[str], None] = '0019_wb_orders_supply'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # /v1/journal (60-сек тик консоли) и /v1/trace выбирают события по КМ:
    # без индекса каждый опрос — полный скролл растущей таблицы событий
    op.create_index('ix_events_km_kind', 'events', ['km', 'kind'], schema='journal')


def downgrade() -> None:
    op.drop_index('ix_events_km_kind', table_name='events', schema='journal')
