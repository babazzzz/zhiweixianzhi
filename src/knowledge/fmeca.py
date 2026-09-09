"""FMECA query facade over the normalized SQLite rule table."""

from __future__ import annotations

from src.database.connection import Database


class FMECARepository:
    """Read-only business facade for structured failure modes and SOPs."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def get_rule(self, fault_code: int) -> dict[str, object] | None:
        """Return the rule matching a fault code."""

        return self.database.get_fmeca(fault_code)

    def list_rules(self) -> list[dict[str, object]]:
        """Return all FMECA rules."""

        return self.database.list_fmeca()

    def search(self, query: str) -> list[dict[str, object]]:
        """Find rules whose name, mechanism or signals mention the query."""

        needle = query.lower()
        return [rule for rule in self.list_rules() if needle in str(rule).lower()]
