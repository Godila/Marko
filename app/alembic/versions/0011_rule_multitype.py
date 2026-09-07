"""rules.product_types — список видов товара (мультивыбор); drop nkmt.brands

Справочник брендов удалён: правила с мультивыбором видов полностью закрывают
его роль (бренд без видов = «справочник» запись). Одинокое значение
product_type конвертируется в одноэлементный список.

Revision ID: 0011_rule_multitype
Revises: 0010_nkmt_brands
Create Date: 2026-09-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0011_rule_multitype'
down_revision: Union[str, Sequence[str], None] = '0010_nkmt_brands'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('rules', 'product_type', new_column_name='product_types',
                    type_=postgresql.JSONB(), nullable=False,
                    postgresql_using="CASE WHEN btrim(product_type) = '' "
                                     "THEN '[]'::jsonb "
                                     "ELSE jsonb_build_array(product_type) END",
                    schema='nkmt')
    op.drop_table('brands', schema='nkmt')


def downgrade() -> None:
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
    op.alter_column('rules', 'product_types', new_column_name='product_type',
                    type_=sa.String(), nullable=False,
                    postgresql_using="CASE WHEN jsonb_array_length(product_types) = 0 "
                                     "THEN '' ELSE product_types->>0 END",
                    schema='nkmt')
