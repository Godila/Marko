"""nkmt.sets: наборы (is_set) в Нацкаталоге — флаг карточки + компоненты

Набор = карточка со своим GTIN (generate-gtins как у обычных), в фид уходит
is_set + set_gtins[]. Компоненты ссылаются артикулом нашей карточки (gtin
может ещё не существовать — резолв на подаче) или внешним GTIN.

Revision ID: 0022_nkmt_sets
Revises: 0021_wb_client_returns
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '0022_nkmt_sets'
down_revision: Union[str, Sequence[str], None] = '0021_wb_client_returns'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('cards', sa.Column('is_set', sa.Boolean(), nullable=False,
                                     server_default=sa.text('false')), schema='nkmt')
    op.create_table(
        'set_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('card_id', sa.Integer(), sa.ForeignKey('nkmt.cards.id',
                                                        ondelete='CASCADE'),
                  nullable=False),
        sa.Column('gtin', sa.String(32), nullable=False, server_default=''),
        sa.Column('article_src', sa.String(64), nullable=False, server_default=''),
        sa.Column('quantity', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        schema='nkmt')
    op.create_index('ix_nkmt_set_items_card', 'set_items', ['card_id'], schema='nkmt')


def downgrade() -> None:
    op.drop_index('ix_nkmt_set_items_card', table_name='set_items', schema='nkmt')
    op.drop_table('set_items', schema='nkmt')
    op.drop_column('cards', 'is_set', schema='nkmt')
