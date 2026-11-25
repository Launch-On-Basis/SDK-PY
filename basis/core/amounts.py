"""Formatters that turn raw amounts of one token into ``Amount`` objects (TypeScript SDK: src/core/amounts.ts).

Write results resolve the formatter BEFORE sending (it may read the chain; the reads are cached), so building the
result of a confirmed tx never needs an RPC call and never raises.
"""

from typing import Callable, Optional

from web3 import Web3

from ..config.addresses import is_zero_address
from .mains import MainInfo, main_amount, pair_amount
from .units import Amount, to_amount

AmountOf = Callable[[int], Amount]


def opt_amount(fmt: AmountOf, raw: Optional[int]) -> Optional[Amount]:
    """``fmt(raw)``, or None for a figure whose event is missing."""
    return None if raw is None else fmt(raw)


def pair_amount_of(info: MainInfo) -> AmountOf:
    """Formatter in the pair asset of ``info`` (scaled by its ``pair_unit``)."""
    return lambda raw: pair_amount(raw, info)


def main_amount_of(info: MainInfo) -> AmountOf:
    """Formatter in the main token of ``info`` (18 decimals)."""
    return lambda raw: main_amount(raw, info)


def amount_of(client, token: str, info: Optional[MainInfo] = None) -> AmountOf:
    """Formatter for raw amounts of ``token``: a main is unit ``main`` (that main alone is loaded: ``mains.get``); the
    pair asset of ``info``, or of a main the address book lists, is unit ``pairAsset``; anything else is unit ``token``.
    Only the main in use is looked up, never every main, so one stale address-book entry cannot block a write on
    another main: a pair asset that is not ``info``'s is told by the book (its label only), and its decimals and symbol
    come from the token itself, as for any token."""
    t = token.lower()
    if info is not None:
        if t == info.main.lower():
            return main_amount_of(info)
        if t == info.pair_asset.lower():
            return pair_amount_of(info)
    swap_live = not is_zero_address(client.addresses.get("swap"))
    if swap_live and client.mains.is_main(token):
        return main_amount_of(client.mains.get(token))
    meta = client.mains.token_meta(token)
    decimals, symbol = int(meta["decimals"]), str(meta["symbol"])
    asset = Web3.to_checksum_address(token)
    pair = swap_live and any(not is_zero_address(m.get("pairAsset")) and str(m["pairAsset"]).lower() == t
                             for m in client.addresses.get("mains") or [])
    unit = "pairAsset" if pair else "token"
    return lambda raw: to_amount(raw, decimals, unit, asset, symbol)
