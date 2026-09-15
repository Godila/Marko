"""platform.users / platform.sessions — вход в консоль по логину и паролю

Cookie-сессии (HttpOnly/Secure/SameSite=Lax, TTL 7 дней, скользящее продление);
Bearer-токены (platform.tokens) продолжают работать как раньше — dual-auth в
deps.require_scope. Пароли: scrypt stdlib, параметры закодированы в строке хэша.

Revision ID: 0013_console_auth
Revises: 0012_wb_orders
Create Date: 2026-09-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0013_console_auth'
down_revision: Union[str, Sequence[str], None] = '0012_wb_orders'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('principal_id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('scopes', sa.Text(), nullable=False,
                  server_default='read,docs:submit,nkmt:import'),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fail_until', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['principal_id'], ['platform.principals.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('principal_id'),
        sa.UniqueConstraint('username'),
        schema='platform')
    op.create_table('sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('principal_id', sa.Integer(), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scopes', sa.Text(), nullable=False,
                  server_default='read,docs:submit,nkmt:import'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['principal_id'], ['platform.principals.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
        schema='platform')
    op.create_index('ix_sessions_expires_at', 'sessions', ['expires_at'], schema='platform')


def downgrade() -> None:
    op.drop_index('ix_sessions_expires_at', table_name='sessions', schema='platform')
    op.drop_table('sessions', schema='platform')
    op.drop_table('users', schema='platform')
