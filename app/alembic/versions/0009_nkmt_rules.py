"""nkmt.rules — правила РД: бренд × вид товара → декларация/производитель

Revision ID: 0009_nkmt_rules
Revises: 0008_item_withdrawn_by
Create Date: 2026-09-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0009_nkmt_rules'
down_revision: Union[str, Sequence[str], None] = '0008_item_withdrawn_by'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('rules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('brand', sa.String(), nullable=False),
    sa.Column('product_type', sa.String(), nullable=False),
    sa.Column('declaration_id', sa.Integer(), nullable=False),
    sa.Column('producer', sa.String(), nullable=False),
    sa.ForeignKeyConstraint(['declaration_id'], ['nkmt.declarations.id'],
                            ondelete='RESTRICT'),
    sa.CheckConstraint("btrim(brand) <> '' OR btrim(product_type) <> ''",
                       name='ck_rules_condition'),
    sa.PrimaryKeyConstraint('id'),
    schema='nkmt'
    )


def downgrade() -> None:
    op.drop_table('rules', schema='nkmt')
