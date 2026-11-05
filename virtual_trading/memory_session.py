"""S74 in-memory session for ``paper_bets``: the stand-in the replay uses in place of the database.

It supports exactly the statement shapes the paper ledger and the trader read with (S71, S72):

- ``select(PaperBet)`` with an AND of ``==``, ``<=`` or ``in_`` criteria on PaperBet columns (or no criteria), and
  ``.scalars().all()`` on the result;
- ``add_all(rows)`` and ``flush()``.

Anything else (a raw ``text()`` statement, another entity, ``order_by``, ``OR``, an unknown operator) raises
``NotImplementedError`` instead of answering wrongly.

Column defaults are applied the way the database and the ORM apply them at insert: a column the object never set
takes its declared default (``status='pending'``, ``in_play=False``, ``reason=''``, ...), and a missing primary key
``bet_id`` takes a UUID from a seeded generator, so two runs over the same inputs produce the same ids.
"""

from __future__ import annotations

import operator
import random
import uuid
from collections.abc import Iterable
from typing import Any, Optional

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList
from sqlalchemy.sql.selectable import Select

from backend.models.paper_bets import PaperBet

ID_SEED = 20261103  # seed of the bet_id generator, so ids are the same on every run

_COMPARE = {
    operator.eq: operator.eq,
    operator.ne: operator.ne,
    operator.lt: operator.lt,
    operator.le: operator.le,
    operator.gt: operator.gt,
    operator.ge: operator.ge,
}


def _operator_name(op: Any) -> str:
    return getattr(op, "__name__", str(op))


def _eval(clause: Any, row: Any) -> bool:
    """Evaluate a WHERE clause against one ORM row."""
    if isinstance(clause, BooleanClauseList):
        if _operator_name(clause.operator) != "and_":
            raise NotImplementedError(f"memory session supports AND only, got {_operator_name(clause.operator)}")
        return all(_eval(c, row) for c in clause.clauses)
    if not isinstance(clause, BinaryExpression) or not isinstance(clause.left, Column):
        raise NotImplementedError(f"memory session cannot evaluate {type(clause).__name__}")
    value = getattr(row, clause.left.key)
    target = clause.right.value
    name = _operator_name(clause.operator)
    if name == "in_op":
        return value in target
    if clause.operator in _COMPARE:
        return _COMPARE[clause.operator](value, target)
    raise NotImplementedError(f"memory session does not support the operator {name}")


class _Result:
    """The subset of a SQLAlchemy result the ledger uses."""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> "_Result":
        return self

    def all(self) -> list[Any]:
        return list(self._rows)


class MemorySession:
    """In-memory ``paper_bets`` with the SELECT, add_all and flush shapes of the ledger. Nothing is written anywhere."""

    def __init__(self, rows: Iterable[Any] = (), *, seed: int = ID_SEED) -> None:
        self.rows: list[Any] = []
        self._rng = random.Random(seed)
        self.add_all(rows)

    # ------------------------------------------------------------------ writes
    def add_all(self, rows: Iterable[Any]) -> None:
        batch = list(rows)
        for row in batch:
            # The ledger sets bet_id to uuid4() (S71). Every row gets the next seeded id instead, so two runs give the
            # same ids. Ids are keys only: no decision or metric reads them.
            if not isinstance(row, PaperBet):
                raise NotImplementedError(f"memory session stores PaperBet rows only, got {type(row).__name__}")
            row.bet_id = self._seeded_uuid()
            self._apply_defaults(row)
        self.rows.extend(batch)

    async def flush(self) -> None:
        for row in self.rows:
            self._apply_defaults(row)

    def _apply_defaults(self, row: Any) -> None:
        if not isinstance(row, PaperBet):
            raise NotImplementedError(f"memory session stores PaperBet rows only, got {type(row).__name__}")
        for column in PaperBet.__table__.columns:
            if column.default is None:
                continue
            # The ORM sends a default for an attribute never set, and for a missing primary key (bet_id None).
            unset = column.name not in row.__dict__ or (column.primary_key and getattr(row, column.name) is None)
            if unset:
                setattr(row, column.name, self._default_value(column))

    def _seeded_uuid(self) -> uuid.UUID:
        return uuid.UUID(int=self._rng.getrandbits(128), version=4)

    def _default_value(self, column: Any) -> Any:
        if isinstance(column.type, UUID):
            return self._seeded_uuid()  # the seeded stand-in for uuid4
        if column.default.is_scalar:
            return column.default.arg
        raise NotImplementedError(f"memory session has no rule for the default of {column.name}")

    # ------------------------------------------------------------------ reads
    async def execute(self, stmt: Any) -> _Result:
        if not isinstance(stmt, Select):
            raise NotImplementedError(f"memory session runs select() statements only, got {type(stmt).__name__}")
        entities = [d.get("entity") for d in stmt.column_descriptions]
        if entities != [PaperBet]:
            raise NotImplementedError("memory session selects PaperBet rows only")
        if stmt._order_by_clauses:  # noqa: SLF001
            raise NotImplementedError("memory session does not support ORDER BY")
        criteria: Optional[Any] = stmt.whereclause
        matched = [r for r in self.rows if criteria is None or _eval(criteria, r)]
        return _Result(matched)


__all__ = ["ID_SEED", "MemorySession"]
