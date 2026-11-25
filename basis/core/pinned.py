"""Reads pinned to one block (TypeScript SDK: src/core/pinned.ts).

A pinned read names a block the SDK just got from one node. On load-balanced nodes, at about 10 blocks a second, the next
read can land on a node a few blocks behind, which answers "unknown block" (geth: "header not found") for a block that
exists. That one class is worth a short retry at the same block: the node catches up within a few hundred ms. Everything
else (a revert, state a node no longer keeps, a transport failure) goes straight to the caller.

After a confirmed write, the signer's next writes (for 30 s) read at that write's block or later (``BaseModule._send``
and its allowance check): on a chain with a block time they first wait a moment for the RPC's latest block to reach it
(:func:`at_least_block`), and a node still behind it after the retries is ``BasisRpcError`` ``server_error`` ("the RPC
node is behind block N"; :func:`retry_pinned_at`), never a revert. On a chain without one (a local node) a pin above the
node's head is from before a rollback and is not used (:func:`pinned_block`).
"""

import re
import time
from typing import Any, Callable, Optional, TypeVar

from web3.exceptions import BlockNotFound

from .revert import _messages, _rpc_error

T = TypeVar("T")

_UNKNOWN_BLOCK_RE = re.compile(
    r"unknown block|(?:header|block)\b.{0,60}\b(?:not found|could not be found)|beyond (?:the )?(?:current )?head", re.I)

PINNED_RETRY_DELAYS_S = (0.1, 0.2, 0.4)
PIN_WAIT_S = 3.0
PIN_POLL_S = 0.2


def is_unknown_block_error(err: Any) -> bool:
    """True when a node answered that it does not know the block a read named (it may only be behind)."""
    return isinstance(err, BlockNotFound) or any(_UNKNOWN_BLOCK_RE.search(m) for m in _messages(err))


def retry_pinned(read: Callable[[], T]) -> T:
    """Runs ``read``, retrying it after :data:`PINNED_RETRY_DELAYS_S` while it fails with
    :func:`is_unknown_block_error`."""
    for delay in PINNED_RETRY_DELAYS_S:
        try:
            return read()
        except Exception as e:  # noqa: BLE001
            if not is_unknown_block_error(e):
                raise
            time.sleep(delay)
    return read()


def behind_block_error(cause: Any, block: int, contract: Optional[str] = None,
                       function_name: Optional[str] = None) -> Exception:
    """``BasisRpcError`` ``server_error`` for a node that still does not know ``block`` (``cause``: its answer): "the RPC
    node is behind block N". Nothing was sent; the same call works again in a moment. TS: behindBlockError."""
    return _rpc_error(cause, "server_error", contract, function_name,
                      detail=f"The RPC node is behind block {block}: it does not know that block yet. Try again in a moment.")


def retry_pinned_at(read: Callable[[], T], block: int, contract: Optional[str] = None,
                    function_name: Optional[str] = None) -> T:
    """``read`` pinned to ``block``, through :func:`retry_pinned`: a node still behind that block after the retries is
    :func:`behind_block_error` (never a revert); any other failure is raised as it came. TS: retryPinnedAt."""
    try:
        return retry_pinned(read)
    except Exception as e:  # noqa: BLE001
        if is_unknown_block_error(e):
            raise behind_block_error(e, block, contract, function_name) from e
        raise


def at_least_block(latest: Callable[[], Any], block: int, wait_s: Optional[float] = None,
                   poll_s: Optional[float] = None) -> int:
    """``block``, or the RPC's latest block (``latest()``) when that is later: a pinned read never names older state than
    it must (a node keeps only recent state). While the latest is still below ``block`` (a node behind the one that
    answered with it), the block number is read again every ``poll_s`` (default :data:`PIN_POLL_S`), up to ``wait_s``
    (default :data:`PIN_WAIT_S`), so the pinned read after it does not start on a node that does not know the block
    yet. Never raises (a failed read gives ``block``). TS: atLeastBlock."""
    wait = PIN_WAIT_S if wait_s is None else wait_s
    poll = PIN_POLL_S if poll_s is None else poll_s
    until = time.monotonic() + wait
    while True:
        try:
            n = int(latest())
        except Exception:  # noqa: BLE001
            return block
        if n >= block:
            return n
        if time.monotonic() >= until:
            return block
        time.sleep(poll)


def pinned_block(latest: Callable[[], Any], pin: int, block_time_s: float) -> Optional[int]:
    """The block a signer's read goes at while ``pin`` (the block of its last confirmed write) is live, or None for "at
    latest". On a chain with a block time (``block_time_s`` > 0: load-balanced nodes, some a few blocks behind):
    :func:`at_least_block` (the RPC's latest once it reached ``pin``, else ``pin`` after a short wait). On a chain without
    one (a local node: one node, no lag): the RPC's latest when it reached ``pin``, else None at once: a pin above the
    node's head is from before a rollback (a reverted snapshot), and no block above the head can be read. Never raises
    (a failed read gives ``pin``). TS: pinnedBlock."""
    if block_time_s > 0:
        return at_least_block(latest, pin)
    try:
        n = int(latest())
    except Exception:  # noqa: BLE001
        return pin
    return n if n >= pin else None


def pinned_call(fn: Any, block: int) -> Any:
    """``fn.call(block_identifier=block)`` (``fn``: a contract function with its arguments) through :func:`retry_pinned`."""
    return retry_pinned(lambda: fn.call(block_identifier=block))
