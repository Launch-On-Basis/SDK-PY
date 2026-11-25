"""Address books per chain. Keys are camelCase, same as the TS SDK.

A zero address means "not deployed": the module using it raises ``NotDeployedError`` without an RPC call.
(TypeScript SDK: src/config/addresses.ts.)
"""

import copy
import re
from typing import Any, Dict, List, Optional, TypedDict

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


class MainEntry(TypedDict, total=False):
    """One main token with its own pair asset."""
    key: str
    label: str
    main: str
    pairAsset: str
    vault: str
    pairSymbol: str
    pairDecimals: int


ADDRESS_KEYS = (
    "swap", "factory", "taxes", "leverage",
    "mainLens",
    "loanHub", "vesting", "liquidator", "hopExecutor",
    "tranche",
    "tranchePrivate",
    "resolver", "trancheReader", "redemptionReader",
    "upDown",
    "bondToken",
    "wrappedNative",
    "referrals",
    "erc8004Registry",
)

OPTIONAL_ADDRESS_KEYS = (
    "extraRankedHost",
)


def empty_address_book() -> Dict[str, Any]:
    book: Dict[str, Any] = {k: ZERO_ADDRESS for k in ADDRESS_KEYS}
    book["mains"] = []
    book["defaultMain"] = "stasis-usdg"
    return book


EMPTY_ADDRESS_BOOK: Dict[str, Any] = empty_address_book()

ROBINHOOD_ADDRESSES: Dict[str, Any] = {
    **empty_address_book(),
    "swap": ZERO_ADDRESS,
    "factory": ZERO_ADDRESS,
    "taxes": ZERO_ADDRESS,
    "leverage": ZERO_ADDRESS,
    "mainLens": ZERO_ADDRESS,
    "loanHub": ZERO_ADDRESS,
    "vesting": ZERO_ADDRESS,
    "liquidator": ZERO_ADDRESS,
    "hopExecutor": ZERO_ADDRESS,
    "tranche": ZERO_ADDRESS,
    "tranchePrivate": ZERO_ADDRESS,
    "resolver": ZERO_ADDRESS,
    "trancheReader": ZERO_ADDRESS,
    "redemptionReader": ZERO_ADDRESS,
    "upDown": ZERO_ADDRESS,
    "bondToken": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
    "wrappedNative": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
    "referrals": "0xd324299e3487b5efcd6C431FD45363E41D41F151",
    "erc8004Registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    "mains": [
        {
            "key": "stasis-usdg", "label": "STASIS",
            "main": "0x0000000000000000000000000000000000000000",
            "pairAsset": "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168",
            "vault": "0x0000000000000000000000000000000000000000",
            "pairSymbol": "USDG", "pairDecimals": 6,
        },
    ],
    "defaultMain": "stasis-usdg",
}

ROBINHOOD_PAPER_ADDRESSES: Dict[str, Any] = {
    **empty_address_book(),
    "swap": "0x16Ce58e72238863d3393d58AbF0dfa705687075C",
    "factory": "0xaDb496644CC9F8a35070d3CEb55304806d9225Ac",
    "taxes": "0x3FFA5a096EEc9Acef021407b603Fdfd5aaa1Aa18",
    "leverage": "0x347db256245cCa305619956B710802251F2f5Df9",
    "mainLens": "0x01A3590F517FB0c823770d198243e5E385991b2C",
    "loanHub": "0xA8BDA97206a6ed705df9ad83cF4bfa26DEa2b112",
    "vesting": "0xBF2271C5CB6c019f73D381a4861fE05f9a6d7934",
    "liquidator": "0xBCC685A09787D3243D3319031B665EE4B50466eF",
    "hopExecutor": "0x3cAA7A748866725b5990330ACb1A3F8B282E55aA",
    "tranche": "0xC076115de69F820E1Ea321e76ef6127d4ACe5517",
    "tranchePrivate": "0x7233C395ecC1bf4337cfEfCA4c3a910cfEd7500c",
    "resolver": "0x585A5E0dA4251b734319894dC56e8fa0Fa5cA93B",
    "trancheReader": "0x3eA49942B43Ab1491894dD5B48e5092f5FACbC7E",
    "redemptionReader": "0x859ff587d4D083dFb295361668cAca1033a3ABB1",
    "upDown": "0x053CBf59DaA076Ba783b1f5De5147d52dC00521B",
    "bondToken": "0x477b7223Ce57f920A5e8F215112362bDA0d2B1f9",
    "wrappedNative": "0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73",
    "referrals": "0x05261333646541007088249108C85d4055915FAd",
    "erc8004Registry": "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432",
    "mains": [
        {
            "key": "stasis-usdg", "label": "STASIS",
            "main": "0x541610A1868C6801d785c33BF9987a73c0992C7D",
            "pairAsset": "0x477b7223Ce57f920A5e8F215112362bDA0d2B1f9",
            "vault": "0x628a02aF3C1Ed7bd45Cf71b3014aB7b83d9206cd",
            "pairSymbol": "tUSDG", "pairDecimals": 6,
        },
        {
            "key": "tnvdaplus-tnvda", "label": "tNVDA+",
            "main": "0xd234BD98222D3c213356b978E7A7334FE69B5333",
            "pairAsset": "0xc6614201D7789FCB5b7B782D58De19ea8B900821",
            "vault": "0x628a02aF3C1Ed7bd45Cf71b3014aB7b83d9206cd",
            "pairSymbol": "tNVDA", "pairDecimals": 18,
        },
        {
            "key": "tethplus-teth", "label": "tETH+",
            "main": "0x4a33306B5777d97efa9b41Aa907E9171cDD52c97",
            "pairAsset": "0x7d2F1D4e3Af725CAE4D1b795352f3aB6323B1dED",
            "vault": "0x628a02aF3C1Ed7bd45Cf71b3014aB7b83d9206cd",
            "pairSymbol": "tETH", "pairDecimals": 18,
        },
    ],
    "defaultMain": "stasis-usdg",
}


_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_ZERO_RE = re.compile(r"0x0{40}")
_MAIN_ENTRY_KEYS = frozenset(("key", "label", "main", "pairAsset", "vault", "pairSymbol", "pairDecimals"))


def is_zero_address(address: Optional[str]) -> bool:
    """"Not deployed": absent (None / "") or the zero address (``0x`` + 40 zeros). The one zero check of the SDK (TS
    ``isZeroAddress`` is the same): a short form such as ``0x0`` is not an address, so it is not zero either (the
    address book refuses it in :func:`merge_address_book`)."""
    if not address:
        return True
    return isinstance(address, str) and _ZERO_RE.fullmatch(address) is not None


def _book_address(v: Any, where: str) -> str:
    from web3 import Web3

    from ..core.errors import ValidationError
    if not isinstance(v, str) or _ADDRESS_RE.fullmatch(v) is None:
        shown = f'"{v}"' if isinstance(v, str) else _shown(v)
        raise ValidationError("addresses", f"addresses.{where} must be an address (0x + 40 hex digits), got {shown}.")
    body = v[2:]
    if body != body.lower() and body != body.upper() and Web3.to_checksum_address(v) != v:
        raise ValidationError("addresses",
                              f"addresses.{where} has a bad EIP-55 checksum ({v}): check it, or pass it all lower-case.")
    return Web3.to_checksum_address(v)


def _shown(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return "undefined" if v is None else str(v)


def _book_main(m: Any, i: int) -> Dict[str, Any]:
    from ..core.errors import ValidationError
    from ..core.opts import did_you_mean
    if not isinstance(m, dict):
        raise ValidationError("addresses", f"addresses.mains[{i}] must be an object, got {_shown(m)}.")
    for k in m:
        if k not in _MAIN_ENTRY_KEYS:
            order = ("key", "label", "main", "pairAsset", "vault", "pairSymbol", "pairDecimals")
            raise ValidationError("addresses", f'addresses.mains[{i}] has an unknown key "{k}".{did_you_mean([k], order)}')
    key = m.get("key")
    if not isinstance(key, str) or not key:
        raise ValidationError("addresses", f"addresses.mains[{i}].key must be a non-empty string.")
    if m.get("label") is not None and not isinstance(m["label"], str):
        raise ValidationError("addresses", f"addresses.mains[{i}].label must be a string.")
    if m.get("pairSymbol") is not None and not isinstance(m["pairSymbol"], str):
        raise ValidationError("addresses", f"addresses.mains[{i}].pairSymbol must be a string.")
    dec = m.get("pairDecimals")
    if dec is not None and (isinstance(dec, bool) or not isinstance(dec, int) or dec < 0 or dec > 255):
        raise ValidationError("addresses", f"addresses.mains[{i}].pairDecimals must be a whole number 0-255, got {_shown(dec)}.")
    out: Dict[str, Any] = {
        "key": key, "label": m.get("label") if m.get("label") is not None else key,
        "main": _book_address(m.get("main"), f"mains[{i}].main"),
        "pairAsset": _book_address(m.get("pairAsset"), f"mains[{i}].pairAsset"),
        "vault": _book_address(m.get("vault"), f"mains[{i}].vault"),
    }
    if m.get("pairSymbol") is not None:
        out["pairSymbol"] = m["pairSymbol"]
    if dec is not None:
        out["pairDecimals"] = dec
    return out


def merge_address_book(base: Dict[str, Any], overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Shallow-merge overrides over a preset; ``mains`` is replaced as a whole when given. Every override is checked
    first (``ValidationError("addresses")``): a known key only, addresses ``0x`` + 40 hex digits (checksummed on the
    way in; the zero address = not deployed), ``mains`` entries with a key and their three addresses, ``defaultMain`` a
    non-empty string. ``None`` values are skipped."""
    from ..core.errors import ValidationError
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValidationError("addresses", f"addresses must be a dict of address-book overrides, got {_shown(overrides)}.")
    single = set(ADDRESS_KEYS) | set(OPTIONAL_ADDRESS_KEYS)
    out = copy.deepcopy(base)
    for k, v in overrides.items():
        if v is None:
            continue
        if k == "mains":
            if not isinstance(v, (list, tuple)):
                raise ValidationError("addresses", f"addresses.mains must be a list, got {_shown(v)}.")
            out["mains"] = [_book_main(m, i) for i, m in enumerate(v)]
        elif k == "defaultMain":
            if not isinstance(v, str) or not v:
                raise ValidationError("addresses", f"addresses.defaultMain must be a non-empty string, got {_shown(v)}.")
            out["defaultMain"] = v
        elif k in single:
            out[k] = _book_address(v, k)
        else:
            from ..core.opts import did_you_mean
            names = [*ADDRESS_KEYS, *OPTIONAL_ADDRESS_KEYS, "mains", "defaultMain"]
            known = ", ".join(names)
            raise ValidationError("addresses", f'addresses has an unknown key "{k}" (known: {known}).{did_you_mean([k], names)}')
    return out
