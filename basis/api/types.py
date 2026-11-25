"""Shared API types (spec components). Domain types live in their domain module. TypeScript SDK: src/api/types.ts."""

from typing import Any, Dict, List, Optional

try:
    from typing import TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict

RawAmount = str
"""Raw on-chain integer as a decimal string, in the units of the asset named beside it."""

PublicPersonRef = str
"""Opaque public person reference (``person1_...``): what public lists show in place of a wallet. Not a wallet address,
not a credential and not a contract argument. ``get_feed_by_wallet`` takes it."""

UsdE18 = Optional[str]
"""USD x 1e18 as a decimal string; None = unknown (never 0, never 1:1). Comes with a ``usdSource``."""

UsdSource = Optional[str]
"""Where a USD value came from: fixed | feed | hop | offchain; None when the value is None."""

E18 = str
"""A ratio or price x 1e18 as a decimal string."""

Cursor = str
"""History cursor ``blockNumber:logIndex`` (pass ``nextCursor`` back as ``cursor``)."""


class Pagination(TypedDict, total=False):
    """The one ``pagination`` object: histories use ``nextCursor``, sorted lists ``page`` / ``total``."""

    limit: int
    hasMore: bool
    nextCursor: Optional[str]
    page: int
    total: int


class CursorPage(TypedDict):
    """A cursor-paged list."""

    data: List[Dict[str, Any]]
    pagination: Pagination


class ApiErrorBody(TypedDict, total=False):
    """Every API error body (``error`` and ``code`` always present)."""

    error: str
    code: str
    allowed: List[str]
    feature: str
