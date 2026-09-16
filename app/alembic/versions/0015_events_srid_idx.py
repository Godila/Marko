"""journal.events: частичный индекс по srid — lookup заказа WB из консоли

Revision ID: 0015_events_srid_idx
Revises: 0014_cis_status
Create Date: 2026-09-16
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0015_events_srid_idx'
down_revision: Union[str, Sequence[str], None] = '0014_cis_status'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # manual-события пишут srid='' — в индекс не попадают; varchar_pattern_ops
    # обслуживает и точное сравнение, и LIKE 'doc.%'
    op.create_index('ix_events_srid', 'events', ['srid'], schema='journal',
                    postgresql_where=sa.text("srid <> ''"),
                    postgresql_ops={'srid': 'varchar_pattern_ops'})


def downgrade() -> None:
    op.drop_index('ix_events_srid', schema='journal')
