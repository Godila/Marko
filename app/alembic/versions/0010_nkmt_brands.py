"""nkmt.brands — справочник брендов: бренд → производитель/декларация

4-й источник подстановок (файл > правило > справочник бренда > дефолт).

Revision ID: 0010_nkmt_brands
Revises: 0009_nkmt_rules
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0010_nkmt_brands'
down_revision: Union[str, Sequence[str], None] = '0009_nkmt_rules'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('brands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('producer', sa.String(), nullable=False),
    sa.Column('declaration_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['declaration_id'], ['nkmt.declarations.id'],
                            ondelete='RESTRICT'),
    sa.CheckConstraint("btrim(name) <> ''", name='ck_brands_name'),
    sa.PrimaryKeyConstraint('id'),
    schema='nkmt'
    )


def downgrade() -> None:
    op.drop_table('brands', schema='nkmt')
