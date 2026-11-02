"""agent usage by model

Subagents can run on a different model from their parent (Haiku under Opus), so one run's tokens are recorded
per model for an accurate cost.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-02 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('agent_execution_records',
                  sa.Column('usage_by_model', postgresql.JSONB(astext_type=sa.Text()), server_default='{}',
                            nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('agent_execution_records', 'usage_by_model')
