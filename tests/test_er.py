"""The committed ER diagram matches the models (regenerate: uv run finrecon docs er)."""

from finrecon_ai.db.base import Base
from finrecon_ai.db.er import DOC, GROUPS, document


def test_the_er_diagram_is_current():
    assert DOC.read_text() == document(), "docs/er-diagram.md is stale: run `uv run finrecon docs er`"


def test_every_table_is_listed_once():
    listed = [t for names in GROUPS.values() for t in names]
    assert sorted(listed) == sorted(Base.metadata.tables) and len(listed) == len(set(listed))
