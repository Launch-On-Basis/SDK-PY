"""Event decoding for write results (TypeScript SDK: src/core/events.ts).

Every write returns the figures decoded from its own events. Only logs emitted by the expected contract address count.
An event that is missing after the tx confirmed never raises: the result field is None and ``result["warnings"]`` says
why.
"""

import logging
from typing import Any, Dict, List, Sequence, Union

from web3.logs import DISCARD

logger = logging.getLogger("basis")


def decode_events(contract: Any, receipt: Any, event_name: str,
                  address: Union[str, Sequence[str], None] = None) -> List[Dict[str, Any]]:
    """The decoded args (plain dicts keyed by the ABI names, e.g. ``{"tokenAddress": ...}``) of every ``event_name``
    log in ``receipt`` emitted by ``address`` (one address or several; default ``contract.address``), in log order.
    ``contract``: a web3 contract whose ABI has the event. Logs of other contracts, other events and logs that do not
    decode are ignored. An empty list when there is none."""
    if address is None:
        address = contract.address
    want = {a.lower() for a in ([address] if isinstance(address, str) else address)}
    logs = [lg for lg in (receipt.get("logs") or []) if str(lg.get("address", "")).lower() in want]
    if not logs:
        return []
    events = getattr(contract.events, event_name)().process_receipt({**dict(receipt), "logs": logs}, errors=DISCARD)
    return [dict(e["args"]) for e in events if str(e["address"]).lower() in want]


def missing_event_warning(contract: str, event_name: str, tx_hash: str, fields: Sequence[str] = ()) -> str:
    """The text of a missing-event warning (same wording in both SDKs). ``fields``: the result fields left None."""
    left = f": {', '.join(fields)} {'is' if len(fields) == 1 else 'are'} null" if fields else ""
    return (f"{contract} {event_name} event not found in the logs of {tx_hash}{left}. "
            "The transaction is confirmed: do not resend it.")


def add_missing_event_warning(tx: Dict[str, Any], contract: str, event_name: str, fields: Sequence[str] = ()) -> str:
    """Appends a missing-event warning to ``tx["warnings"]`` (and logs it). Use after a confirmed write when an
    expected event is absent; set the result fields it would have filled to None. Never raises."""
    msg = missing_event_warning(contract, event_name, tx.get("hash", "?"), fields)
    tx.setdefault("warnings", []).append(msg)
    logger.warning("[basis-sdk] %s", msg)
    return msg

