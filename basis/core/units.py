"""Amounts with their unit, and slippage helpers (TypeScript SDK: src/core/units.ts)."""

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Optional, Union

from .errors import ValidationError

AmountUnit = Literal["pairAsset", "main", "token"]


@dataclass(frozen=True)
class Amount:
    """An amount or price with everything needed to read it. Never a bare "USD" number."""
    raw: int
    formatted: str
    decimals: int
    unit: AmountUnit
    asset: str
    symbol: str


def format_units(raw: int, decimals: int) -> str:
    """Same output as viem's formatUnits: no trailing zeros, no exponent."""
    negative = raw < 0
    s = str(abs(raw)).rjust(decimals + 1, "0")
    whole, frac = (s[:-decimals], s[-decimals:]) if decimals else (s, "")
    frac = frac.rstrip("0")
    out = f"{whole}.{frac}" if frac else whole
    return f"-{out}" if negative else out


def to_amount(raw: int, decimals: int, unit: AmountUnit, asset: str, symbol: str) -> Amount:
    return Amount(raw=raw, formatted=format_units(raw, decimals), decimals=decimals, unit=unit, asset=asset, symbol=symbol)


MAX_UINT256 = (1 << 256) - 1
_DECIMAL_RE = re.compile(r"([+-]?)(\d*)(?:\.(\d*))?(?:[eE]([+-]?\d{1,6}))?", re.ASCII)


def parse_amount(value: Union[int, str, float, Decimal], decimals: int, field: str = "amount") -> int:
    """Parse a human amount ("12.5", "1e-6", 0.25, Decimal) into raw units; an int is taken as raw already. Digits
    beyond ``decimals`` are truncated toward zero (never more than asked). Negative, non-numeric, bool or above-uint256
    values raise ``ValidationError(field)``."""
    if isinstance(value, bool) or not isinstance(value, (int, str, float, Decimal)):
        raise ValidationError(field, f"{field} must be a number or a decimal string, got {value!r}.")
    if isinstance(value, int):
        raw = value
    else:
        m = _DECIMAL_RE.fullmatch(str(value).strip(" \t\n\r\f\v"))
        if not m or not (m.group(2) or m.group(3)):
            raise ValidationError(field, f"{field} is not a valid amount: {value}.")
        frac = m.group(3) or ""
        digits = (m.group(2) + frac).lstrip("0")
        if digits and m.group(1) == "-":
            raise ValidationError(field, f"{field} must not be negative, got {value}.")
        shift = int(m.group(4) or 0) - len(frac) + decimals
        if not digits:
            raw = 0
        elif len(digits) + shift > 78:
            raise ValidationError(field, f"{field} is too large: {value}.")
        else:
            raw = int(digits + "0" * shift) if shift >= 0 else int(digits[:max(0, len(digits) + shift)] or "0")
    if raw < 0:
        raise ValidationError(field, f"{field} must not be negative, got {value}.")
    if raw > MAX_UINT256:
        raise ValidationError(field, f"{field} is too large: {value}.")
    return raw


BPS = 10_000

DEFAULT_SLIPPAGE_BPS = {"trade": 100, "updown": 300}


def validate_slippage_bps(bps: Any, field: str = "slippageBps") -> int:
    """A slippage tolerance in bps as an int: a whole number from 0 to 9999 (an int, or a whole float like TS numbers;
    bools, fractions and anything else refused with ``ValidationError("slippageBps")``). ``field`` names it in the
    message."""
    whole = (isinstance(bps, int) and not isinstance(bps, bool)) or (isinstance(bps, float) and bps.is_integer())
    if not whole or bps < 0 or bps >= BPS:
        raise ValidationError("slippageBps", f"{field} must be a whole number of bps from 0 to 9999, got {_js_str(bps)}.")
    return int(bps)


def min_with_slippage(quote: int, bps: int) -> int:
    """A minimum from a quote: quote * (1 - bps), rounded down. ``bps``: 0-9999 (:func:`validate_slippage_bps`)."""
    b = validate_slippage_bps(bps)
    return quote * (BPS - b) // BPS


def max_with_slippage(quote: int, bps: int) -> int:
    """A maximum from a quote: quote * (1 + bps), rounded up. ``bps``: 0-9999 (:func:`validate_slippage_bps`)."""
    b = validate_slippage_bps(bps)
    return -(-(quote * (BPS + b)) // BPS)


MAX_SAFE_FLOAT_INT = (1 << 53) - 1


def to_uint(v: Union[int, float], field: str) -> int:
    """A non-negative whole-number argument as int (at most uint256), or a ValidationError naming the field. A float
    must be a whole number of at most 2^53 - 1: above that it has already lost precision, so pass an int."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not v.is_integer()):
        raise ValidationError(field, f"{field} must be a whole number, got {v!r}.")
    if isinstance(v, float) and abs(v) > MAX_SAFE_FLOAT_INT:
        raise ValidationError(field, f"{field} is above 2^53 as a float and has lost precision ({v!r}): pass an int.")
    b = int(v)
    if b < 0:
        raise ValidationError(field, f"{field} must not be negative, got {b}.")
    if b > MAX_UINT256:
        raise ValidationError(field, f"{field} is too large: {b} (above uint256).")
    return b


def opt_uint(v: Any, field: str) -> Optional[int]:
    """An optional whole-number argument (:func:`to_uint` under ``field``), None when omitted."""
    return None if v is None else to_uint(v, field)


def to_address(v: Any, field: str) -> str:
    """A strict address argument, checksummed: 0x plus 40 hex characters. All lower or all upper case passes; a
    mixed-case address must carry a correct EIP-55 checksum (a typo there is refused, never re-checksummed into a
    valid-looking address nobody controls). Anything else raises ``ValidationError(field)``. Use it on every address a
    caller types, before any read or approval. (TypeScript SDK: ``toAddress``.)"""
    from web3 import Web3

    if not isinstance(v, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", v, re.ASCII):
        shown = f"'{v}'" if isinstance(v, str) else _js_str(v)
        raise ValidationError(field, f"{field} must be a 0x address (40 hex characters), got {shown}.")
    body = v[2:]
    one_case = body == body.lower() or body == body.upper()
    if not one_case and not Web3.is_checksum_address(v):
        raise ValidationError(
            field,
            f"{field} has a wrong checksum (a mistyped character?): {v}. Copy the address again, or pass it all in lower case.",
        )
    return Web3.to_checksum_address(v)


def check_text(v: Any, field: str) -> None:
    """Text that goes on chain or into a signed hash must be well-formed: a lone (unpaired) surrogate can't be encoded
    as UTF-8. Raises ``ValidationError(field)`` with "<field> is not valid UTF-8 text." (TypeScript SDK: ``checkText``, same
    rule and message). Non-strings are left to the caller's own type check."""
    if not isinstance(v, str):
        return
    try:
        v.encode("utf-8")
    except UnicodeEncodeError as e:
        raise ValidationError(field, f"{field} is not valid UTF-8 text.") from e


_NO_DEFAULT: Any = object()


def to_bool(v: Any, field: str, default: Any = _NO_DEFAULT) -> bool:
    """A strict boolean argument: only ``True`` / ``False`` pass (no truthiness: ``"false"``, ``1``, ``0`` are refused
    with ``ValidationError(field)``). When ``default`` is given, ``None`` returns it (optional parameter); without it,
    ``None`` is refused too."""
    if v is None and default is not _NO_DEFAULT:
        return default
    if not isinstance(v, bool):
        shown = f"'{v}'" if isinstance(v, str) else _js_str(v)
        raise ValidationError(field, f"{field} must be true or false, got {shown}.")
    return v


def _js_str(v: Any) -> str:
    """``v`` as TS ``String(v)`` shows it in messages (true/false, None as undefined/null)."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "undefined"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
