"""Sync domain: /api/v1/sync. The write pipeline posts through client.sync_tx (core/sync.py, with retries and a queue),
not this single-attempt wrapper. TypeScript SDK: src/api/sync.ts."""

from typing import Dict, List, Literal, Optional

from .core import ApiCore
from .types import TypedDict


class SyncEvent(TypedDict):
    """One stored BASIS log of the transaction: ``type`` the event name (or ``unknown``); ``inserted`` False when this
    call found the row already stored."""

    type: str
    contractAddress: str
    logIndex: int
    inserted: bool


class _SyncUpDown(TypedDict):
    """Kept for shape (``events`` is always 0)."""

    success: bool
    events: int


class _SyncTransactionResponseBase(TypedDict):
    success: bool
    status: Literal["synced", "already_synced", "partial", "ignored"]
    chainId: int
    txHash: str
    blockNumber: Optional[int]
    confirmed: bool
    events: List[SyncEvent]
    inserted: int
    typed: Dict[str, int]
    loan: None
    updown: _SyncUpDown


class SyncTransactionResponse(_SyncTransactionResponseBase, total=False):
    """200 answer of ``POST /api/v1/sync``. ``status`` ``ignored``: the transaction has no BASIS event; ``partial``:
    some typed rows failed (see ``projectError``). ``inserted``: raw rows written by this call; ``typed``: typed rows
    written per table by this call (empty for ``already_synced`` and ``ignored``); ``loan`` always None (kept for
    shape)."""

    error: Optional[str]
    projectError: Optional[str]


class SyncApi(ApiCore):
    """Sync routes (mixin of ``BasisAPI``)."""

    def sync_transaction(self, tx_hash: str) -> SyncTransactionResponse:
        """Store every BASIS event of a transaction (trades, loans, vault, vesting, markets and the order book,
        resolution, Up/Down, token and market creation).

        ``POST /api/v1/sync {txHash, source: "sdk"}``

        One attempt: a non-2xx raises BasisApiError (422 ``code``: pending | reverted | sync_failed; 503
        ``chain_mismatch``; 429 ``rate_limited``). No auth; 60 a minute per IP. Idempotent. Returns
        :class:`SyncTransactionResponse`.
        """
        return self._request("POST", "/api/v1/sync", body={"txHash": tx_hash, "source": "sdk"})
