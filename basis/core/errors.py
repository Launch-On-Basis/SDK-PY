"""Errors the SDK raises itself (contract reverts are ``BasisContractError`` in revert.py)."""

import re
from decimal import Decimal
from typing import Optional

from .redact import redact_urls


class BasisError(Exception):
    """Base class for SDK errors."""


class NotDeployedError(BasisError):
    """The contract this call needs has a zero address on the current chain, or its feature is not enabled yet
    (``message``: Up/Down, ``FEATURES["up_down"]``). No RPC call was made."""

    def __init__(self, contract: str, chain_id: int, message: Optional[str] = None):
        super().__init__(
            f"[basis-sdk] {message}" if message else
            f"[basis-sdk] {contract} is not deployed on chain {chain_id} (zero address). "
            'Pass its address via the "addresses" option or wait for the deployment.'
        )
        self.contract = contract
        self.chain_id = chain_id


class ValidationError(BasisError):
    """A client-side pre-check failed: the transaction would revert, so it was not sent."""

    def __init__(self, rule: str, message: str):
        super().__init__(f"[basis-sdk] {message}")
        self.rule = rule


RPC_ERROR_KINDS = {
    "network": "the RPC node could not be reached",
    "timeout": "the RPC node did not answer in time",
    "rate_limited": "the RPC node rate-limited the request",
    "server_error": "the RPC node failed (server error)",
    "insufficient_funds": "the signer cannot pay the value plus gas (insufficient funds)",
    "rejected": "the RPC node refused the transaction",
    "nonce_used": "nonce already used by another transaction",
}


_FUNDS_NUMBERS = (re.compile(r"balance (\d+),\s*tx cost (\d+)"), re.compile(r"have (\d+) want (\d+)"))


def _eth(wei: int) -> str:
    return format((Decimal(wei) / Decimal(10 ** 18)).normalize(), "f")


def _rpc_detail(kind: str, cause: object) -> str:
    """What ``cause`` says, for a ``BasisRpcError`` message: the node's own message when the error carries the JSON-RPC
    answer (web3's text is the whole answer as a dict), else its text, with every URL cut down to its scheme and host
    (an RPC URL can carry an API key). An insufficient-funds answer with numbers is given in ETH. TS: ``rpcDetail``."""
    if cause is None:
        return ""
    text = str(cause)
    resp = getattr(cause, "rpc_response", None)
    err = resp.get("error") if isinstance(resp, dict) else None
    if not isinstance(err, dict):
        from .revert import _bad_format_error
        err = _bad_format_error(cause)
    node = err.get("message") if isinstance(err, dict) and isinstance(err.get("message"), str) and err["message"].strip() else None
    if kind == "insufficient_funds":
        for rx in _FUNDS_NUMBERS:
            m = rx.search(f"{node or ''} {text}")
            if m:
                return (f"The wallet holds {_eth(int(m[1]))} ETH and this transaction needs {_eth(int(m[2]))} ETH up "
                        "front (gas limit x max fee, plus any value), though it spends less.")
    return redact_urls(node.strip() if node else text)


class BasisRpcError(BasisError):
    """An RPC / transport failure or a node refusal BEFORE the transaction was broadcast (simulation, gas estimate,
    nonce, fees, or a definitive refusal of the signed transaction). Nothing was sent: retry once the cause is fixed.
    Not a contract revert (those are ``BasisContractError``). After a broadcast, failures are
    ``TxNotConfirmedError`` instead (the tx may still confirm).

    ``kind``: ``network`` (unreachable, connection reset, an answer cut short or not JSON, an HTTP error other than
    429 / 5xx), ``timeout`` (no answer in time, or the node's own timeout: JSON-RPC -32002, "context deadline
    exceeded", "execution aborted (timeout = 5s)"), ``rate_limited`` (HTTP 429, JSON-RPC -32005), ``server_error``
    (HTTP 5xx, JSON-RPC internal error, a node that could not reach what is behind it, does not know a block yet or is
    still building its transaction index),
    ``insufficient_funds`` (the signer cannot pay value + gas), ``rejected`` (the node refused the signed transaction
    for another reason: underpriced, gas limit, ...), ``nonce_used`` (the nonce was already taken by another
    transaction: this write never ran and never will, so it can be sent again). ``http_status`` / ``rpc_code``: the
    HTTP status / JSON-RPC code of the answer when there was one, else None. ``cause``: the underlying web3 / requests
    error. ``detail``: the text after "nothing was sent." (default: what ``cause`` says, the node's own message when it
    carries one)."""

    def __init__(self, kind: str, contract: "str | None" = None, function_name: "str | None" = None,
                 http_status: "int | None" = None, rpc_code: "int | None" = None, cause: object = None,
                 detail: "str | None" = None):
        where = f"{contract or 'contract'}.{function_name or 'call'}: " if (contract or function_name) else ""
        text = _rpc_detail(kind, cause) if detail is None else detail
        super().__init__(f"[basis-sdk] {where}{RPC_ERROR_KINDS.get(kind, kind)}; nothing was sent.{f' {text}' if text else ''}")
        self.kind = kind
        self.http_status = http_status
        self.rpc_code = rpc_code
        self.contract = contract
        self.function_name = function_name
        self.cause = cause
        self.__cause__ = cause if isinstance(cause, BaseException) else None


class WalletRequiredError(BasisError):
    """A write needs a signer (private_key)."""

    def __init__(self):
        super().__init__("[basis-sdk] Wallet required for write operations. Create the client with a private_key.")


class TxNotConfirmedError(BasisError):
    """The transaction was sent (it has a hash) but its receipt could not be read: the wait timed out
    (``receipt_timeout_ms``) or the RPC failed. It may still confirm, so DO NOT resend the write: check ``tx_hash``
    first. Its backend sync is already queued (``client.pending_syncs``); ``client.flush_pending_syncs()`` delivers it
    once the tx is mined. ``note`` (also at the end of the message) says how to finish what the method would have done
    after the tx (e.g. agent.register: call register_and_sync(config, tx_hash) once it is mined), or None."""

    def __init__(self, tx_hash: str, contract: str, function_name: str, cause: object = None,
                 note: "str | None" = None):
        super().__init__(
            f"[basis-sdk] {contract}.{function_name} was sent (tx {tx_hash}) but its receipt could not be read: "
            f"{redact_urls(cause)} Do not resend it: check the hash first. Its sync is queued "
            "(client.flush_pending_syncs())."
            + (f" {note}" if note else "")
        )
        self.tx_hash = tx_hash
        self.contract = contract
        self.function_name = function_name
        self.cause = cause
        self.note = note or None

    def with_note(self, note: str) -> "TxNotConfirmedError":
        """The same error with ``note`` (the follow-up a module method adds for the steps after its tx)."""
        return TxNotConfirmedError(self.tx_hash, self.contract, self.function_name, self.cause, note)


class TxReplacedError(BasisError):
    """The transaction was sent but another transaction with the same nonce was mined instead, so the intended call
    never executed: ``reason`` 'cancelled' (a zero-value transfer to self, e.g. the wallet's "cancel") or 'replaced' (a
    different call, value or recipient). Nothing of the intended write happened apart from the replacement's own
    effects; the original hash will never be mined (its sync is not posted). A 'replaced' replacement that succeeded is
    synced (``replacement_sync``: the backend records it if it was a BASIS action and ignores it otherwise); a cancel is
    not. A speed-up (same call, higher fee: 'repriced') is not an error: the write returns the mined hash with
    ``replaced_hash``."""

    def __init__(self, original_hash: str, replacement_hash: str, reason: str, contract: str, function_name: str,
                 replacement_receipt: object = None, replacement_sync: "dict | None" = None):
        what = ("it was cancelled in the wallet (a zero-value transfer to self took its nonce)" if reason == "cancelled"
                else "another transaction with the same nonce (a different call) was mined in its place")
        super().__init__(
            f"[basis-sdk] {contract}.{function_name} did not execute: {what}. Sent {original_hash}, mined "
            f"{replacement_hash} instead. The original will never be mined; check the replacement before sending again."
        )
        self.original_hash = original_hash
        self.replacement_hash = replacement_hash
        self.reason = reason
        self.contract = contract
        self.function_name = function_name
        self.replacement_receipt = replacement_receipt
        self.replacement_sync = replacement_sync
