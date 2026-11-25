"""Cross routes, shared by ``trading.swap_cross`` and the bet quote chain (``market_reader.quote_bet_input``): the
steps of a route, the refusal of a route by them, and a route's outside hop checked against what its caps allow a swap
sent now. Internal: not exported from the package. TS: src/core/crossHop.ts.

A route token (how a cross route treats a token) is a dict {address, kind: 'main' | 'token' | 'pair', main, pair}: a
main (``main`` itself, ``pair`` its pair asset), a factory token (``main`` its main, ``pair`` that main's pair asset;
both None on a token whose main is not a known main) or anything else ('pair': a pair asset or an outside token; both
None).
"""

from typing import Any, Callable, Dict, List, Optional, Sequence

from web3 import Web3

from ..config.addresses import is_zero_address
from .errors import ValidationError
from .loans import quote_hop_within_caps
from .units import BPS

_STEP_SELL_TOKEN = 0
_STEP_SELL_MAIN = 1
_STEP_OUTSIDE = 2
_STEP_BUY_MAIN = 3
_STEP_BUY_TOKEN = 4

HopQuote = Callable[[str, str, str, int], int]


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _eq(a: str, b: str) -> bool:
    return str(a).lower() == str(b).lower()


def route_token_of(client, token: str) -> Dict[str, Any]:
    """How a cross route treats ``token`` (a route token dict): a main (paused or not, as ``mains.is_main``), a
    factory token (none when the factory is not deployed), or anything else."""
    token = _cs(token)
    mains = client.mains
    if mains.is_main(token):
        info = mains.get(token)
        return {"address": token, "kind": "main", "main": _cs(info.main), "pair": _cs(info.pair_asset)}
    if not is_zero_address(client.addresses["factory"]) and client.factory.is_ecosystem_token(token):
        main = _cs(mains._contract(token, "EcosystemToken").functions.quoteToken().call())
        if not mains.is_main(main):
            return {"address": token, "kind": "token", "main": None, "pair": None}
        return {"address": token, "kind": "token", "main": main, "pair": _cs(mains.get(main).pair_asset)}
    return {"address": token, "kind": "pair", "main": None, "pair": None}


def _step_kinds(path: Sequence[str], rt: Sequence[Dict[str, Any]]) -> List[int]:
    """The kind of each step of ``path``, from how the route treats each token (``rt``, with every main on the path
    live): a factory token into its own main (sell token), a main into its pair asset (sell main), a pair asset into its
    main (buy main), a main into a factory token on it (buy token), anything else through the outside exchange."""
    kinds = []
    for i in range(len(path) - 1):
        a, b, ta, tb = path[i], path[i + 1], rt[i], rt[i + 1]
        if ta["kind"] == "token" and ta["main"] and _eq(ta["main"], b) and tb["kind"] == "main":
            kinds.append(_STEP_SELL_TOKEN)
        elif ta["kind"] == "main" and ta["pair"] and _eq(ta["pair"], b):
            kinds.append(_STEP_SELL_MAIN)
        elif tb["kind"] == "main" and tb["pair"] and _eq(tb["pair"], a):
            kinds.append(_STEP_BUY_MAIN)
        elif ta["kind"] == "main" and tb["kind"] == "token" and tb["main"] and _eq(tb["main"], a):
            kinds.append(_STEP_BUY_TOKEN)
        else:
            kinds.append(_STEP_OUTSIDE)
    return kinds


def _cross_plan_error(path: Sequence[str], kinds: Sequence[int]) -> Optional[ValidationError]:
    """Why a swap refuses a cross route by its step kinds (``_step_kinds``), checked in the swap's order, or None: each
    step at most once and in order ("Bad route order"), a sell step first or a buy step last ("no BASIS leg"), and no
    main sold into its pair asset and bought back ("round trip": the sell side's main is the buy side's)."""
    n = len(kinds)
    if any(k <= kinds[i - 1] for i, k in enumerate(kinds) if i > 0):
        return ValidationError("Bad route order",
                               "The steps of the route are out of order or repeated. A cross swap takes each step at "
                               "most once, in this order: sell a factory token, sell a main, one outside exchange hop, "
                               "buy a main, buy a factory token (build_cross_path builds the route).")
    sell = kinds[0] <= _STEP_SELL_MAIN
    buy = kinds[n - 1] >= _STEP_BUY_MAIN
    if not sell and not buy:
        return ValidationError("no BASIS leg",
                               "The route neither sells nor buys a BASIS token: a cross swap starts by selling a main "
                               "or a factory token, or ends by buying one.")
    if sell and buy and _STEP_SELL_MAIN in kinds and _STEP_BUY_MAIN in kinds:
        sell_main = path[1] if kinds[0] == _STEP_SELL_TOKEN else path[0]
        buy_main = path[n - 1] if kinds[n - 1] == _STEP_BUY_TOKEN else path[n]
        if _eq(sell_main, buy_main):
            return ValidationError("round trip",
                                   f"The route sells {sell_main} into its pair asset and buys it back (a round trip): "
                                   "go through it once, without the pair asset (build_cross_path builds the route).")
    return None


def check_cross_plan(path: Sequence[str], rt: Sequence[Dict[str, Any]]) -> None:
    """A cross route refused by its steps as a swap refuses them (``rt``: how the route treats each token of ``path``,
    ``route_token_of``): "Bad route order", "no BASIS leg", "round trip". No reads. ``check_cross_hop`` runs it first
    too. TS: ``checkCrossPlan``."""
    refused = _cross_plan_error(path, _step_kinds(path, rt))
    if refused is not None:
        raise refused


def cross_sides(path: Sequence[str], rt: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The sides of a cross route a swap takes (``check_cross_plan`` passed): {sell, buy, split}. ``sell`` when its
    first step sells a factory token or a main, ``buy`` when its last step buys one, each {main, pair (that main's pair
    asset), token (the factory token it sells or buys; None: the main itself)}, None when the route has no such side.
    ``split`` True when both sides are there on two different mains (each side then pays part of the base fee), else
    one side pays it. No reads. TS: ``crossSides``."""
    kinds = _step_kinds(path, rt)
    n = len(kinds)
    sell: Optional[Dict[str, Any]] = None
    buy: Optional[Dict[str, Any]] = None
    if n:
        if kinds[0] == _STEP_SELL_TOKEN:
            sell = {"main": _cs(path[1]), "pair": _cs(rt[1]["pair"]), "token": _cs(path[0])}
        elif kinds[0] == _STEP_SELL_MAIN:
            sell = {"main": _cs(path[0]), "pair": _cs(path[1]), "token": None}
        if kinds[n - 1] == _STEP_BUY_TOKEN:
            buy = {"main": _cs(path[n - 1]), "pair": _cs(rt[n - 1]["pair"]), "token": _cs(path[n])}
        elif kinds[n - 1] == _STEP_BUY_MAIN:
            buy = {"main": _cs(path[n]), "pair": _cs(path[n - 1]), "token": None}
    split = sell is not None and buy is not None and not _eq(sell["main"], buy["main"])
    return {"sell": sell, "buy": buy, "split": split}


def check_cross_hop(client, path: Sequence[str], rt: Sequence[Dict[str, Any]], amount_in: int, trader: str,
                    quote: HopQuote) -> None:
    """A cross route's steps (from ``rt``: how the route treats each token of ``path``, ``route_token_of``), refused as
    a swap refuses them ("Bad route order", "no BASIS leg", "round trip"); then its outside hop, if it has one, against
    what its caps allow a swap sent now: the amount that reaches it (``amount_in`` through the sell steps, less the sell
    side's fee as ``trader``), then what the exchange pays for it (``quote_hop_within_caps`` on the trades' hop
    executor, the payout from ``quote``; "above cap", "no executor", "no route"). The on-chain swap quote checks the hop
    against its full caps only: what other swaps used of them in the current window on Robinhood Chain is not counted
    there. Its own reads (the hop executor, the route, the caps and the hop fee, the sell-step quote
    ``trading.get_amounts_out``, the fee rates) are not classified: a failure there comes as the RPC client raised it.
    A failure of the hop quote is ``quote``'s to classify."""
    check_cross_plan(path, rt)
    kinds = _step_kinds(path, rt)
    if _STEP_OUTSIDE not in kinds:
        return
    o, n = kinds.index(_STEP_OUTSIDE), len(kinds)
    sell, buy = o > 0, o < n - 1
    h_in, h_out = path[o], path[o + 1]
    ex = client.trading.get_hop_executor()
    if is_zero_address(ex):
        raise ValidationError("no executor",
                              "This route swaps through an outside exchange, and SWAP has no hop executor yet.")
    if len(client.mains._contract(ex, "HopExecutor").functions.getRoute(h_in, h_out).call()) == 0:
        raise ValidationError("no route", f"No outside route from {h_in} to {h_out}.")
    x = amount_in
    if sell:
        taxes = client.taxes
        base = taxes.get_base_rate()
        sell_main = rt[0]["main"] if kinds[0] == _STEP_SELL_TOKEN else path[0]
        buy_main = None if not buy else (rt[n]["main"] if kinds[n - 1] == _STEP_BUY_TOKEN else path[n])
        bps = base // 2 if buy_main and not _eq(sell_main, buy_main) else base
        gross = client.trading.get_amounts_out(amount_in, path[:o + 1])
        rate = taxes.get_tax_rate(path[0], trader) if bps == base else taxes.get_tax_rate_base(path[0], trader, bps)
        x = gross - gross * rate // BPS
    quote_hop_within_caps(client, ex, h_in, h_out, x, lambda v: quote(ex, h_in, h_out, v))
