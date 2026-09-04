"""wb.returns (монитор возвратов WB goods-return)

Revision ID: 0007_wb_returns
Revises: 0006_nkmt
Create Date: 2026-09-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0007_wb_returns'
down_revision: Union[str, Sequence[str], None] = '0006_nkmt'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE SCHEMA IF NOT EXISTS wb')
    op.create_table('returns',
    sa.Column('srid', sa.String(length=64), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('status', sa.String(length=128), nullable=False),
    sa.Column('expired_dt', sa.String(length=32), nullable=False),
    sa.Column('alerted_new', sa.Boolean(), nullable=False),
    sa.Column('alerted_deadline', sa.Boolean(), nullable=False),
    sa.Column('payload', postgresql.JSON(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('srid'),
    schema='wb'
    )


def downgrade() -> None:
    op.drop_table('returns', schema='wb')
    op.execute('DROP SCHEMA IF EXISTS wb CASCADE')
