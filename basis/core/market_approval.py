"""Market approvals: BASIS approves each new market with an EIP-712 signature (the AI review, ``api.approve_market``).
Checked here the way the market host checks it, so a bad approval is caught before gas or the single-use signature is
spent. TypeScript SDK: src/core/marketApproval.ts.

Approval dicts (``api.approve_market``) use snake_case keys: an approved one is
``{'approved': True, 'decision': 'approve', 'market_type', 'signed': {'name','symbol','outcomes','endTime'}, 'signature',
'deadline' (int, unix seconds), 'content_hash', 'verifying_contract', 'signer', + optional 'chain_id', 'host'
(the host to call ``create_market`` on), 'escalated', 'model'}``; a rejection ``{'approved': False, 'decision': 'reject',
'category'?, 'reason'?, ...}``. A route error (400/401/413/429/500/503) raises BasisApiError with its ``code``.
"""

import re
from typing import Any, Optional, Sequence, Union

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_typed_data
from web3 import Web3

from .errors import ValidationError
from .units import check_text, to_address

MARKET_APPROVAL_DOMAIN = {"name": "BASIS Markets", "version": "1"}

MARKET_APPROVAL_TYPES = {
    "MarketApproval": [
        {"name": "creator", "type": "address"},
        {"name": "contentHash", "type": "bytes32"},
        {"name": "deadline", "type": "uint256"},
    ],
}

_HALF_N = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0

_HEX_RE = re.compile(r"0x[0-9a-fA-F]*")


def _h(s: str) -> bytes:
    return bytes(Web3.keccak(text=s))


def _kind_of(v: Any) -> str:
    """What a value is in a refusal message, in the words of TS ``typeof`` (as the API's outcomes check words it):
    None is "null"."""
    if v is None:
        return "null"
    return {str: "string", bool: "boolean", int: "number", float: "number"}.get(type(v), "object")


def market_content_hash(name: str, symbol: str, outcomes: Sequence[str], end_time: Optional[int] = 0) -> str:
    """The approval's ``contentHash`` of a market, as the host computes it.
    ``name`` and ``symbol`` must be str and ``outcomes`` a list/tuple of str (anything else is ``ValidationError``
    'name' / 'symbol' / 'outcomes', never converted to text). They are hashed as UTF-8, so each must be well-formed
    text: a lone surrogate is refused with the same rules (:func:`check_text`). ``end_time`` None: 0. Same rules and
    messages in TS. Returns lowercase 0x-hex."""
    for v, field in ((name, "name"), (symbol, "symbol")):
        if not isinstance(v, str):
            raise ValidationError(field, f"{field} must be a string, got {_kind_of(v)}.")
    if not isinstance(outcomes, (list, tuple)) or any(not isinstance(o, str) for o in outcomes):
        got = "an array with a non-string entry" if isinstance(outcomes, (list, tuple)) else _kind_of(outcomes)
        raise ValidationError("outcomes", f"outcomes must be an array of answer strings, got {got}.")
    check_text(name, "name")
    check_text(symbol, "symbol")
    for o in outcomes:
        check_text(o, "outcomes")
    if end_time is None:
        end_time = 0
    options_hash = bytes(Web3.keccak(b"".join(_h(o) for o in outcomes)))
    return "0x" + bytes(Web3.keccak(abi_encode(["bytes32", "bytes32", "bytes32", "uint256"],
                                               [_h(name), _h(symbol), options_hash, end_time]))).hex()


def verify_market_approval(
    host: str,
    chain_id: int,
    creator: str,
    name: str,
    symbol: str,
    outcomes: Sequence[str],
    deadline: int,
    signature: Union[str, bytes],
    expected_signer: str,
    content_hash: Optional[str] = None,
    end_time: Optional[int] = 0,
) -> str:
    """Check a market approval locally, the way the host will: a well-formed EIP-712 signature by ``expected_signer``
    (the signer the host accepts: read it from the host), for this host, creator, market strings and deadline.
    ``content_hash`` (the gateway's, when it sent one) must match the local hash.
    ``end_time``: the market's close time sent to ``createMarket`` (default 0, open-ended; None too).
    Returns the recovered signer (checksummed); raises ``ValidationError('Invalid approval', ...)`` on any mismatch
    (a string that is not well-formed text: rule 'name' / 'symbol' / 'outcomes', as ``market_content_hash``).
    Does not check expiry or single use. ``host``, ``creator`` and ``expected_signer`` are strict addresses
    (:func:`to_address`: a wrong checksum is refused, rule ``host`` / ``creator`` / ``expectedSigner``)."""
    host = to_address(host, "host")
    creator = to_address(creator, "creator")
    expected_signer = to_address(expected_signer, "expectedSigner")
    local_hash = market_content_hash(name, symbol, outcomes, end_time)
    if content_hash and content_hash.lower() != local_hash.lower():
        raise ValidationError(
            "Invalid approval",
            f"The approval's contentHash {content_hash} does not match the name/symbol/outcomes/endTime being sent ({local_hash}): "
            "submit the signed strings unchanged.",
        )
    if isinstance(signature, (bytes, bytearray)):
        sig = bytes(signature)
    elif isinstance(signature, str) and _HEX_RE.fullmatch(signature) and len(signature) % 2 == 0:
        sig = bytes.fromhex(signature[2:])
    else:
        sig = b""
    if len(sig) != 65:
        raise ValidationError("Invalid approval", "The approval signature must be 65 bytes (r, s, v).")
    s = int.from_bytes(sig[32:64], "big")
    v = sig[64]
    if v not in (27, 28):
        raise ValidationError("Invalid approval", f"The approval signature has v = {v}; the host accepts only 27 or 28.")
    if s > _HALF_N:
        raise ValidationError("Invalid approval", "The approval signature has a high s value; the host refuses it.")

    try:
        message = encode_typed_data(full_message={
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                **MARKET_APPROVAL_TYPES,
            },
            "primaryType": "MarketApproval",
            "domain": {**MARKET_APPROVAL_DOMAIN, "chainId": int(chain_id), "verifyingContract": host},
            "message": {
                "creator": creator,
                "contentHash": bytes.fromhex(local_hash[2:]),
                "deadline": int(deadline),
            },
        })
        recovered = Account.recover_message(message, signature=sig)
    except Exception as e:  # noqa: BLE001
        raise ValidationError("Invalid approval", f"The approval signature cannot be recovered: {e}") from e
    if recovered.lower() != expected_signer.lower():
        raise ValidationError(
            "Invalid approval",
            f"The approval was signed by {recovered}, not the host's approval signer {expected_signer}. "
            "It was issued for another wallet, host, chain or content, or by another key: request a new approval.",
        )
    return recovered


__all__ = ["MARKET_APPROVAL_DOMAIN", "MARKET_APPROVAL_TYPES", "market_content_hash", "verify_market_approval"]
