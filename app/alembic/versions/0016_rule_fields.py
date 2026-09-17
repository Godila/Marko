"""rules.fields — JSONB-карта дополнительных подстановок правила РД

Правило сверх декларации/производителя может подставлять поля карточки
(whitelist = parse.RULE_FIELDS: размер, цвет, состав, модель, пол, размерная
система, страна) — типовой кейс «one size» для шапок. Существующие правила
получают пустую карту '{}'::jsonb.

Revision ID: 0016_rule_fields
Revises: 0015_events_srid_idx
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0016_rule_fields'
down_revision: Union[str, Sequence[str], None] = '0015_events_srid_idx'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('rules',
                  sa.Column('fields', postgresql.JSONB(), nullable=False,
                            server_default=sa.text("'{}'::jsonb")),
                  schema='nkmt')


def downgrade() -> None:
    op.drop_column('rules', 'fields', schema='nkmt')
