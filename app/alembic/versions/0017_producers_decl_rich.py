"""nkmt.producers — справочник производителей; nkmt.declarations — rich-поля
из ЧЗ (rd/list): статус, срок, продукция, ТНВЭД-список, техрегламенты,
заявитель, изготовитель, момент проверки.

Производители ссылаются из правил/дефолтов текстом (без FK) — создание
таблицы безопасно для существующих данных.

Revision ID: 0017_producers_decl_rich
Revises: 0016_rule_fields
Create Date: 2026-09-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0017_producers_decl_rich'
down_revision: Union[str, Sequence[str], None] = '0016_rule_fields'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('producers',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=512), nullable=False),
        sa.Column('inn', sa.String(length=12), nullable=False,
                  server_default=sa.text("''")),
        sa.Column('kind', sa.String(length=16), nullable=False,
                  server_default=sa.text("''")),
        sa.Column('note', sa.String(length=512), nullable=False,
                  server_default=sa.text("''")),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(),
                  nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        schema='nkmt',
    )
    op.add_column('declarations', sa.Column('status', sa.String(length=64),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('date_to', sa.String(length=10),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('product_name', sa.String(length=1024),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('tnved_list', postgresql.JSONB(),
                  nullable=False, server_default=sa.text("'[]'::jsonb")), schema='nkmt')
    op.add_column('declarations', sa.Column('techregs', sa.String(length=1024),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('applicant', sa.String(length=512),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('manufacturer', sa.String(length=512),
                  nullable=False, server_default=sa.text("''")), schema='nkmt')
    op.add_column('declarations', sa.Column('checked_at', sa.DateTime(),
                  nullable=True), schema='nkmt')


def downgrade() -> None:
    op.drop_table('producers', schema='nkmt')
    for col in ('checked_at', 'manufacturer', 'applicant', 'techregs',
                'tnved_list', 'product_name', 'date_to', 'status'):
        op.drop_column('declarations', col, schema='nkmt')
