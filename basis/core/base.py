"""Shared plumbing for every contract module (TypeScript SDK: src/core/BaseModule.ts)."""

import threading
import time
import weakref
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from .abi import load_abi
from .errors import BasisError, BasisRpcError, NotDeployedError, TxReplacedError, ValidationError, WalletRequiredError
from .events import add_missing_event_warning, decode_events
from .units import MAX_UINT256, min_with_slippage, to_uint, validate_slippage_bps
from ..config.chains import block_time_ms, chain_block_time_ms
from .pinned import pinned_block, retry_pinned_at
from .revert import classify_error, rpc_error_kind

WRITE_PIN_S = 30.0

_pin_lock = threading.Lock()
_write_pins: "weakref.WeakKeyDictionary[Any, Dict[str, tuple]]" = weakref.WeakKeyDictionary()


def _note_pin(client: Any, owner: str, block: int) -> None:
    """Pin ``owner``'s next writes to ``block`` (a write of it was mined there) for :data:`WRITE_PIN_S`; never lowers a
    live pin."""
    with _pin_lock:
        try:
            pins = _write_pins.setdefault(client, {})
        except TypeError:
            return
        now = time.monotonic()
        cur = pins.get(owner.lower())
        keep = cur is not None and now <= cur[1] and cur[0] > int(block)
        pins[owner.lower()] = (cur[0] if keep else int(block), now + WRITE_PIN_S)


def _pin_of(client: Any, sender: str) -> Optional[int]:
    """The block of ``sender``'s last confirmed write, None when there is none or it is older than
    :data:`WRITE_PIN_S`."""
    with _pin_lock:
        try:
            pins = _write_pins.get(client) or {}
        except TypeError:
            return None
        p = pins.get(sender.lower())
        if p is None:
            return None
        if time.monotonic() <= p[1]:
            return p[0]
        pins.pop(sender.lower(), None)
        return None


def _receipt_block(receipt: Any) -> Optional[int]:
    """The ``blockNumber`` of a receipt (a dict or web3 AttributeDict), None when it has none."""
    block = receipt.get("blockNumber") if hasattr(receipt, "get") else None
    return block if isinstance(block, int) and not isinstance(block, bool) else None


class _AtBlock:
    """``function_call`` whose gas estimate runs at ``block`` through :func:`~basis.core.pinned.retry_pinned_at` (a write
    within 30 s of a confirmed write of the signer: a node still behind that block is BasisRpcError ``server_error``);
    everything else is the call's own (``build_transaction``, ``fn_name``, ``contract_abi``, ...)."""

    def __init__(self, function_call: Any, block: int, contract: Optional[str] = None):
        self._fn = function_call
        self._block = block
        self._contract = contract

    def estimate_gas(self, transaction: Any = None, block_identifier: Any = None, **kw: Any) -> int:
        return retry_pinned_at(lambda: self._fn.estimate_gas(transaction, block_identifier=self._block, **kw),
                               self._block, self._contract, getattr(self._fn, "fn_name", None))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._fn, name)


class BaseModule:
    """Deployed-address guard, the write pipeline (simulate -> send -> receipt -> sync) and ERC-20 approvals."""

    def __init__(self, client):
        self.client = client
        self._contracts: Dict[tuple, Any] = {}

    @property
    def web3(self) -> Web3:
        return self.client.web3

    def _require(self, address: Optional[str], contract: str) -> str:
        """Raises NotDeployedError (no RPC call) when ``address`` is zero."""
        if is_zero_address(address):
            raise NotDeployedError(contract, self.client.chain_id)
        return Web3.to_checksum_address(address)

    @property
    def _account(self) -> str:
        """The signer's address. Raises when the client has no wallet."""
        if not self.client.account:
            raise WalletRequiredError()
        return self.client.account.address

    def _contract(self, address: str, abi_name: str):
        """web3 contract for ``address`` with the bundled ABI ``abi_name`` (cached)."""
        key = (address.lower(), abi_name)
        c = self._contracts.get(key)
        if c is None:
            c = self.web3.eth.contract(address=Web3.to_checksum_address(address), abi=load_abi(abi_name))
            self._contracts[key] = c
        return c

    def _pin_for(self, sender: str) -> Optional[int]:
        """The block ``sender``'s reads go at now, or None (at latest) when no write of it was confirmed in the last
        :data:`WRITE_PIN_S`: that write's block, or the RPC's latest when later (a pinned read never names older state
        than it must: a node keeps only recent state). One block-number read; :func:`~basis.core.pinned.pinned_block`
        decides when the RPC is below the pin (a chain with a block time,
        :func:`~basis.config.chains.chain_block_time_ms`: every chain but a local node's, waits a moment for it, up to
        ``PIN_WAIT_S``; a local node's pin above its head is dropped). Never raises (a failed read gives the pin)."""
        pin = _pin_of(self.client, sender)
        if pin is None:
            return None
        cfg = getattr(self.client, "chain_config", None)
        ms = chain_block_time_ms(cfg) if cfg is not None else block_time_ms(getattr(self.client, "chain_id", None))
        return pinned_block(lambda: self.web3.eth.block_number, pin, ms / 1000)

    def _send(self, function_call, contract: str, value: int = 0) -> Dict[str, Any]:
        """Every on-chain write goes through here:
        1. simulate, then estimate the gas (a revert at either step is decoded into BasisContractError; an RPC /
           transport failure or insufficient funds is BasisRpcError; nothing is sent). Within 30 s of a confirmed write
           of the same signer (an approval, the action after it, any write this client saw mined), both run at that
           write's block, or the RPC's latest when later (one block-number read; on a chain with a block time the
           RPC's latest is waited for a moment while it is below that block, and a node that does not know the block
           yet is retried there; one still behind after that is BasisRpcError ``server_error``, "the RPC node is behind
           block N"), never at an older one: a lagging node would still see the state before that write (an allowance
           it spent, a balance). On a chain without a block time (a local node) a pin above the node's head (a
           reverted snapshot) is not used. The gas limit sent is the estimate x 120%, at most 32,000,000 (the chain's
           limit for one transaction; :func:`~basis.core.replacement.gas_limit_for`); an estimate above that is
           ``ValidationError('gas')`` (too big for one transaction). 2. sign and send (the hash is known before the
           broadcast: a lost or unclear answer, anything but the node's own refusal in a JSON-RPC error answer, is
           waited for, never a plain error: a JSON-RPC answer naming the node's own timeout or a failure behind it with
           no revert (-32002, "context deadline exceeded", "i/o timeout", "EOF", a connection refused / reset / closed,
           a failed forward, an upstream error) and an answer without a 32-byte hash included; a JSON-RPC error answer
           in the body of an HTTP 4xx other than 408 / 429 is the node's answer like one sent with HTTP 200,
           :func:`~basis.core.replacement.http_error_answer`; and while the node does not know it and its nonce is
           free the same signed bytes are sent again a few times; the nonce is never below the one after the client's
           last mined transaction while that one is on chain), 3. wait for the receipt (``client.receipt_timeout_ms``;
           RPC blips are polled through), 4. await the backend sync (non-fatal). A receipt wait that fails after
           sending raises TxNotConfirmedError (carries ``tx_hash``) with the hash's sync queued. A status-0 receipt
           raises BasisError (nothing happened on chain apart from the gas).
        The nonce taken by another transaction ("nonce too low" at the broadcast, once the chain shows the transaction
        that took it: another hash mined with the nonce, or the nonce used before this write was signed; until then it
        is waited for like any sent write, up to the receipt timeout, since a node that does not know this hash proves
        nothing; or the same call mined under another hash with it: a local key has no wallet that could speed it up)
        raises BasisRpcError ``nonce_used``: this write never ran and never will, so sending it again is fine.
        A transaction replaced before it was mined by a cancel or a different call with its nonce ('cancelled' /
        'replaced') raises TxReplacedError without syncing the dead hash (a successful 'replaced' replacement is
        synced, a cancel is not).

        Returns {'hash': '0x...', 'receipt': ..., 'result': <simulated return value>, 'warnings': []} (+
        ``replaced_hash`` only when a send step reports a wallet speed-up, which the SDK's local key never does);
        ``warnings`` lists what the caller should know about the confirmed write (e.g. an expected event missing from
        the logs).
        """
        sender = self._account
        fn_name = getattr(function_call, "fn_name", None)
        pin = self._pin_for(sender)
        try:
            if pin is None:
                result = function_call.call({"from": sender, "value": value})
            else:
                result = retry_pinned_at(lambda: function_call.call({"from": sender, "value": value}, block_identifier=pin),
                                         pin, contract, fn_name)
        except Exception as e:  # noqa: BLE001
            raise classify_error(e, contract=contract, function_name=fn_name, abi=getattr(function_call, "contract_abi", None)) from e

        call = function_call if pin is None else _AtBlock(function_call, pin, contract)
        tx = self.client._send_transaction(call, value=value, contract=contract)  # noqa: SLF001
        sent, receipt = tx["hash"], tx["receipt"]
        rep = tx.get("replacement")
        mined_at = _receipt_block(receipt)
        if mined_at is not None:
            _note_pin(self.client, sender, mined_at)
        warnings: List[str] = []
        h, replaced_hash = sent, None
        if rep and rep["reason"] == "nonce_used":
            taken = rep.get("hash") or "an earlier transaction"
            raise BasisRpcError("nonce_used", contract, fn_name, cause=f"nonce {rep.get('nonce')} is taken by {taken}: "
                                "this write did not run and never will, so it can be sent again")
        if rep and rep["hash"].lower() != sent.lower():
            if rep["reason"] != "repriced":
                rtx = rep.get("transaction")
                rto = rtx.get("to") if hasattr(rtx, "get") else None
                rmeta = {"from": sender, "to": rto if isinstance(rto, str) else None}
                rsync = (self.client.sync_tx(rep["hash"], meta=rmeta)
                         if rep["reason"] == "replaced" and receipt.get("status") == 1 else None)
                raise TxReplacedError(sent, rep["hash"], rep["reason"], contract, fn_name or "call",
                                      replacement_receipt=receipt, replacement_sync=rsync)
            h, replaced_hash = rep["hash"], sent
            warnings.append(f"{contract}.{fn_name} was sped up in the wallet: {sent} was replaced by {h} (same call, "
                            "higher fee); `hash` is the mined one.")
        if receipt.get("status") != 1:
            raise BasisError(f"[basis-sdk] {contract}.{fn_name} reverted on chain (tx {h}).")
        to = getattr(function_call, "address", None)
        self.client.sync_tx(h, meta={"from": sender, "to": to if isinstance(to, str) else None})
        out = {"hash": h, "receipt": receipt, "result": result, "warnings": warnings}
        if replaced_hash:
            out["replaced_hash"] = replaced_hash
        return out

    def _events(self, tx: Dict[str, Any], contract: Any, event_name: str,
                address: Union[str, Sequence[str], None] = None) -> List[Dict[str, Any]]:
        """The decoded args of every ``event_name`` log of ``tx`` emitted by ``address`` (default ``contract.address``;
        one or several), in log order (:func:`decode_events`). ``contract``: a web3 contract with the event's ABI."""
        return decode_events(contract, tx["receipt"], event_name, address)

    def _expect_event(self, tx: Dict[str, Any], contract: Any, event_name: str, contract_name: str,
                      fields: Sequence[str] = (), address: Union[str, Sequence[str], None] = None) -> Optional[Dict[str, Any]]:
        """The first ``event_name`` log of ``tx`` emitted by ``address`` (default ``contract.address``), decoded; or
        None with a warning added to ``tx["warnings"]`` naming ``fields`` (the result fields left None). Never raises:
        the tx is confirmed."""
        found = decode_events(contract, tx["receipt"], event_name, address)
        if found:
            return found[0]
        add_missing_event_warning(tx, contract_name, event_name, fields)
        return None

    def _resolve_min(self, explicit: Optional[int], slippage_bps: Optional[int], quote: Callable[[], int],
                     kind: str = "trade", field: str = "minOut") -> int:
        """The minimum to send: an explicit one (a whole number of raw units, :func:`~basis.core.units.to_uint` under
        ``field``; never 0), else the quote minus the slippage (``slippage_bps``, else the client default for
        ``kind``). An RPC / transport failure of the quote is BasisRpcError (nothing was sent); a quote that reverts
        raises as it came."""
        if slippage_bps is not None:
            validate_slippage_bps(slippage_bps)
        if explicit is not None:
            m = to_uint(explicit, field)
            if m == 0:
                raise ValidationError(field, f"A zero {field} is never sent; omit it to derive it from a quote.")
            return m
        q = self._pre_send_read(quote)
        bps = slippage_bps if slippage_bps is not None else self.client.slippage_bps[kind]
        m = min_with_slippage(q, bps)
        if m == 0:
            raise ValidationError(field, f"The quote ({q}) rounds to a zero {field}: the amount is too small.")
        return m

    def _block_deadline(self, seconds: int = 1200) -> int:
        """Latest block timestamp + ``seconds`` (chain time, so a local node's time travel does not break deadlines)."""
        return self._pre_send_read(lambda: self.web3.eth.get_block("latest"))["timestamp"] + seconds

    @staticmethod
    def _pre_send_read(read: Callable[[], Any]) -> Any:
        """Runs a read made before a write is sent (a quote, the deadline's block): an RPC / transport failure becomes
        BasisRpcError (nothing was sent, so you can retry), anything else is re-raised as it came."""
        try:
            return read()
        except Exception as e:  # noqa: BLE001
            if rpc_error_kind(e):
                raise classify_error(e) from e
            raise

    def _ensure_allowance(self, token: str, spender: str, amount: int) -> Optional[Dict[str, Any]]:
        """Approve ``spender`` for ``amount`` of ``token`` when the allowance is short; nothing is sent when it already
        covers it. Trades approve the traded token once, unlimited (``2**256-1``), so later trades of it need no
        approval; every other action approves the exact amount it needs. Within 30 s of a confirmed write of the
        signer the allowance is read at that write's block or later (as :meth:`_send` reads): a node behind it would
        still show an exact approval the last write spent, and the approval would be skipped. After a confirmed
        approval, the signer's next write (the action) is simulated and estimated at the approval's block or later
        (:meth:`_send`)."""
        if amount == 0:
            return None
        owner = self._account
        erc20 = self._contract(token, "ERC20")
        fn = erc20.functions.allowance(owner, Web3.to_checksum_address(spender))
        pin = self._pin_for(owner)
        if pin is None:
            allowance = self._pre_send_read(fn.call)
        else:
            allowance = self._pre_send_read(
                lambda: retry_pinned_at(lambda: fn.call(block_identifier=pin), pin, "ERC20", "allowance"))
        if allowance >= amount:
            return None
        swap = (getattr(self.client, "addresses", None) or {}).get("swap")
        unlimited = not is_zero_address(swap) and spender.lower() == str(swap).lower()
        tx = self._send(erc20.functions.approve(Web3.to_checksum_address(spender), MAX_UINT256 if unlimited else amount),
                        "ERC20")
        block = _receipt_block(tx.get("receipt") if isinstance(tx, dict) else None)
        if block is not None:
            _note_pin(self.client, owner, block)
        return tx

    def _balance_of(self, token: str, owner: Optional[str] = None) -> int:
        """ERC-20 balance of ``owner`` (defaults to the signer)."""
        who = Web3.to_checksum_address(owner) if owner else self._account
        return self._contract(token, "ERC20").functions.balanceOf(who).call()
