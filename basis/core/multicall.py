"""Many contract reads in one ``eth_call`` (TypeScript SDK: src/core/multicall.ts).

``Multicall3.aggregate3`` at the chain preset's ``multicall3`` address (Robinhood Chain: the canonical one); on a chain
without one (a local node, a custom chain) the same contract runs deployless inside the ``eth_call``. Each call may fail on
its own: a failed call raises ``BasisContractError`` (decoded like any other revert) naming its contract and function,
the same as reading it alone.
"""

from typing import Any, List, Optional, Sequence, Tuple

from eth_abi import decode as abi_decode
from eth_abi import encode as abi_encode
from eth_utils.abi import get_abi_output_types
from web3 import Web3
from web3._utils.abi import map_abi_data
from web3._utils.normalizers import BASE_RETURN_NORMALIZERS

from ._multicall_bytecode import DEPLOYLESS_CALL_VIA_BYTECODE, MULTICALL3_BYTECODE
from .pinned import retry_pinned
from .revert import decode_revert

_AGGREGATE3 = Web3.keccak(text="aggregate3((address,bool,bytes)[])")[:4]


CHUNK = 100


def _aggregate(client, calls: Sequence[Tuple[Any, str]], block: Optional[int] = None) -> List[Tuple[bool, bytes]]:
    """``aggregate3`` over ``calls`` (allow-failure each), ``CHUNK`` calls per ``eth_call``: ``(success, returnData)``.
    ``block`` pins every ``eth_call`` to that block (a lagging node's "unknown block" is retried briefly)."""
    w3 = client.web3

    def call(tx):
        if block is None:
            return w3.eth.call(tx)
        return retry_pinned(lambda: w3.eth.call(tx, block))

    mc = getattr(client.chain_config, "multicall3", None)
    out: List[Tuple[bool, bytes]] = []
    for i in range(0, len(calls), CHUNK):
        batch = [(Web3.to_checksum_address(fn.address), True, bytes.fromhex(fn._encode_transaction_data()[2:]))
                 for fn, _ in calls[i:i + CHUNK]]
        data = _AGGREGATE3 + abi_encode(["(address,bool,bytes)[]"], [batch])
        if mc:
            raw = call({"to": Web3.to_checksum_address(mc), "data": "0x" + data.hex()})
        else:
            wrapper = bytes.fromhex(DEPLOYLESS_CALL_VIA_BYTECODE[2:])
            raw = call({"data": "0x" + (wrapper + abi_encode(
                ["bytes", "bytes"], [bytes.fromhex(MULTICALL3_BYTECODE[2:]), data])).hex()})
        (results,) = abi_decode(["(bool,bytes)[]"], bytes(raw))
        out.extend((bool(ok), bytes(ret)) for ok, ret in results)
    return out


def _decode(client, fn: Any, contract: str, ok: bool, ret: bytes) -> Tuple[bool, Any]:
    """``(True, value)`` decoded as ``.call()`` would, or ``(False, BasisContractError)``."""
    name = fn.abi["name"]
    if not ok:
        return False, decode_revert("0x" + ret.hex(), contract, name)
    types = get_abi_output_types(fn.abi)
    try:
        values = map_abi_data(BASE_RETURN_NORMALIZERS, types, client.web3.codec.decode(types, ret))
    except Exception as e:
        return False, decode_revert(e, contract, name)
    return True, values[0] if len(values) == 1 else values


def multicall(client, calls: Sequence[Tuple[Any, str]], block: Optional[int] = None) -> List[Any]:
    """Run ``calls`` (pairs of a bound contract function, e.g. ``c.functions.balanceOf(user)``, and the SDK
    contract name used in errors) in one ``eth_call`` (per ``CHUNK`` calls). Returns each result decoded as ``.call()``
    would; a failed call raises its decoded revert. ``block`` pins every read to that block."""
    if not calls:
        return []
    out: List[Any] = []
    for (fn, contract), (ok, ret) in zip(calls, _aggregate(client, calls, block)):
        good, value = _decode(client, fn, contract, ok, ret)
        if not good:
            raise value
        out.append(value)
    return out


def multicall_settled(client, calls: Sequence[Tuple[Any, str]]) -> List[Tuple[bool, Any]]:
    """``multicall`` that does not raise on a failed call: ``(True, value)`` or ``(False, error)`` per call (a revert,
    or no contract at the address, is the decoded error). An RPC failure of the whole call still raises."""
    if not calls:
        return []
    return [_decode(client, fn, contract, ok, ret) for (fn, contract), (ok, ret) in zip(calls, _aggregate(client, calls))]
