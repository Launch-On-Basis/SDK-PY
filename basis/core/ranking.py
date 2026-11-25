"""Ranking keys for ranked market results. Pure functions, no RPC. Mirrors src/core/ranking.ts.

- Special keys are small integers: bytes32(uint256(253)) EARLY, 254 INVALID, 255 UNRESOLVED.
- A plain uint8 outcome (the legacy uint8 host only) keys as bytes32(uint256(outcome)).
- A ranking (every ranked market, even winner-take-all) keys with :func:`ranking_key`: a one-place ranking [3] does
  not key as bytes32(3).
- An empty ranking is INVALID (KEY_INVALID; the mask is ignored, though the SDK still requires a uint256 int for it).

Keys are lowercase 0x-hex strings (66 chars), like viem's ``Hex``.
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from eth_abi import encode as abi_encode
from web3 import Web3

from .errors import ValidationError

OUTCOME_EARLY = 253
OUTCOME_INVALID = 254
OUTCOME_UNRESOLVED = 255


def _small_key(n: int) -> str:
    return "0x" + format(n, "064x")


KEY_EARLY = _small_key(OUTCOME_EARLY)
KEY_INVALID = _small_key(OUTCOME_INVALID)
KEY_UNRESOLVED = _small_key(OUTCOME_UNRESOLVED)

_MAX_UINT256 = (1 << 256) - 1


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


_MAX_SAFE_FLOAT_INT = (1 << 53) - 1


def whole_number(v: Any) -> Optional[int]:
    """The strict whole-number rule of the SDK for a small integer argument (outcome id, slice count, bps, ...):
    an int (not bool) or a whole float, as JS ``Number.isInteger`` sees a TS number, returned as an int; None for
    anything else (fractions, bools, strings, None). Nothing is truncated: 1.7 is refused, 4000.0 is 4000."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer() and abs(v) <= _MAX_SAFE_FLOAT_INT:
        return int(v)
    return None


def _check_pick(p: Any, i: int) -> int:
    w = whole_number(p)
    if w is None or w < 0 or w > 255:
        raise ValidationError("rankingEntry", f"Ranking entry {i} ({p}) is not an outcome id (an integer 0-255).")
    return w


def normalize_ranking(ranking: Sequence[Any]) -> List[int]:
    """A ranking from untyped input (JSON, an agent), checked before anything is sent: a list/tuple whose every pick
    is a whole number 0-255 (``rankingEntry``; strings, bools and fractions are refused, nothing is truncated; a whole
    float like 1.0 counts as 1, as in TS). Returns the picks as ints."""
    if not isinstance(ranking, (list, tuple)):
        raise ValidationError("ranking", "ranking must be a list of outcome ids.")
    return [_check_pick(p, i) for i, p in enumerate(ranking)]


def _check_mask(tie_mask: Any) -> None:
    if not _is_int(tie_mask) or tie_mask < 0 or tie_mask > _MAX_UINT256:
        raise ValidationError("tieMask", "tie_mask must be a uint256 int.")


def validate_tie_mask(tie_mask: Any) -> int:
    """A tie mask from untyped input, checked before anything is sent: an int 0..2^256-1 (``tieMask``; floats,
    strings and bools are refused, nothing is coerced). Returns it."""
    _check_mask(tie_mask)
    return tie_mask


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def ranking_key(ranking: Sequence[int], tie_mask: int = 0) -> str:
    """The key the resolver stores for (``ranking``, ``tie_mask``); ``[]`` -> KEY_INVALID. ``tie_mask`` must be a
    uint256 int (even for ``[]``), checked first; then ``ranking`` (``normalize_ranking``: a list/tuple, else
    'ranking', of whole numbers 0-255, else 'rankingEntry'), as in TS."""
    _check_mask(tie_mask)
    picks = normalize_ranking(ranking)
    if len(picks) == 0:
        return KEY_INVALID
    base = bytes(Web3.keccak(bytes(picks)))
    if tie_mask == 0:
        return _hex(base)
    return _hex(bytes(Web3.keccak(abi_encode(["bytes32", "uint256"], [base, tie_mask]))))


def validate_ranking(ranking: Sequence[int], tie_mask: int, num_outcomes: int, num_tranches: int) -> None:
    """Client-side check of a ranking, by the host's rules, for a market with ``num_outcomes`` outcomes and
    ``num_tranches`` = ``len(getTrancheBP(market))`` slices. Raises ``ValidationError`` whose ``rule`` names the
    broken rule: ``tranches``, ``rankingLength`` (K..n entries), ``rankingEntry``, ``outcomeRange`` (< n),
    ``duplicate``, ``tieMaskBit0``, ``tieMaskLength`` (no bits at or past the length), ``tieOrder`` (tied entries
    ascending), ``tieBoundary`` (the tie group covering slot K-1 ends the list).
    An empty ranking is INVALID and always legal (the mask is ignored for it). A ``ranking`` that is not a
    list/tuple is ``ranking`` (as ``normalize_ranking``)."""
    if not isinstance(ranking, (list, tuple)):
        raise ValidationError("ranking", "ranking must be a list of outcome ids.")
    if len(ranking) == 0:
        return
    _check_mask(tie_mask)
    n = num_outcomes
    k = num_tranches
    length = len(ranking)
    if k <= 0:
        raise ValidationError("tranches", "The market has no tranches.")
    if length < k or length > n:
        raise ValidationError(
            "rankingLength",
            f"A ranking lists {k} to {n} outcomes (every outcome in the top {k} slots, ties included); got {length}.",
        )
    if tie_mask & 1:
        raise ValidationError("tieMaskBit0", "tie_mask bit 0 must be unset (bit i ties entry i with entry i-1).")
    if tie_mask >> length:
        raise ValidationError("tieMaskLength", f"tie_mask has bits at or past the ranking length ({length}).")

    def tied(i: int) -> bool:
        return bool(tie_mask & (1 << i))

    seen = set()
    picks: List[int] = []
    for i in range(length):
        p = _check_pick(ranking[i], i)
        picks.append(p)
        if p >= n:
            raise ValidationError("outcomeRange", f"Outcome {p} is out of range (the market has {n} outcomes).")
        if i > 0 and tied(i) and p < picks[i - 1]:
            raise ValidationError(
                "tieOrder",
                f"Tied entries must be in ascending outcome id order (entry {i}: {p} after {picks[i - 1]}).",
            )
        if p in seen:
            raise ValidationError("duplicate", f"Outcome {p} is listed twice.")
        seen.add(p)
    slot = 0
    g0 = 0
    while g0 < length:
        g1 = g0 + 1
        while g1 < length and tied(g1):
            g1 += 1
        if slot >= k or (slot + (g1 - g0) >= k and g1 != length):
            raise ValidationError(
                "tieBoundary",
                f"Only outcomes in the top {k} slots are listed: the group covering slot {k - 1} must end the ranking.",
            )
        if slot + (g1 - g0) >= k:
            return
        slot += g1 - g0
        g0 = g1
    raise ValidationError("rankingLength", "The ranking does not reach the last tranche slot.")


def _key_int(key: Union[str, bytes]) -> int:
    if isinstance(key, (bytes, bytearray)):
        return int.from_bytes(key, "big")
    return int(key, 16)


def decode_ranking_key(key: Union[str, bytes]) -> Dict[str, Any]:
    """Classify a key: ``{'kind': ...}`` plus ``'outcome'`` for a plain uint8 key. Values below 256 are small-integer
    keys: 253 ``early``, 254 ``invalid``, 255 ``unresolved``, else a plain uint8 ``outcome`` (legacy uint8 host only).
    Anything else is ``ranking``: resolve it with ``getRanking(market, key)`` on chain. bytes32(0) decodes as
    outcome 0; on the resolver it usually means "unset", which the resolver module reports as None."""
    v = _key_int(key)
    if v >= 256:
        return {"kind": "ranking"}
    if v == OUTCOME_EARLY:
        return {"kind": "early"}
    if v == OUTCOME_INVALID:
        return {"kind": "invalid"}
    if v == OUTCOME_UNRESOLVED:
        return {"kind": "unresolved"}
    return {"kind": "outcome", "outcome": v}


def winner(outcome: int) -> Dict[str, Any]:
    """Winner-take-all result: ``{'ranking': [winner], 'tie_mask': 0}``."""
    return {"ranking": [_check_pick(outcome, 0)], "tie_mask": 0}


__all__ = [
    "OUTCOME_EARLY",
    "OUTCOME_INVALID",
    "OUTCOME_UNRESOLVED",
    "KEY_EARLY",
    "KEY_INVALID",
    "KEY_UNRESOLVED",
    "ranking_key",
    "normalize_ranking",
    "validate_tie_mask",
    "whole_number",
    "validate_ranking",
    "decode_ranking_key",
    "winner",
]
