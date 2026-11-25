"""The hop executor: swaps through an outside exchange, for cross swaps, outside-token bets and repay-with-another-token
(TypeScript SDK: src/modules/HopExecutor.ts). Reads only. Every hop charges a fee on its output (``fee_ppm``, read live), and
each route has caps per contract block (a window of about 12 s on Robinhood Chain).

A hop is a dict: kind (1 = V3-style pool, 2 = Uniswap v4 pool), pool (V3), token_out (what the hop pays
out; WETH for native), currency0 / currency1 / fee / tick_spacing / hooks (the v4 pool key; currency0 0x0 = native).
A route is a dict: token_in, token_out, hops, max_in, max_out (caps in raw units of token_in / token_out); with
``list_routes(with_caps_left=True)`` also in_left / out_left (what the route has left now, None when that read failed).

Caps: each route's usage is counted per contract block. On Robinhood Chain a contract block is a window of about 12 s,
so what the route has left (``caps_left``) is what a swap sent now meets until the window turns over. On a chain whose
contract block is its own (a local node), ``caps_left`` describes the latest mined block and
a swap sent now starts from the full caps (``get_caps``: max_in, max_out before the fee). ``check_caps`` picks the
right one (``chain_config.contract_block_is_l1``).
"""

from typing import Any, Dict, List, Optional

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.revert import decode_revert
from ..core.units import to_address, to_bool, to_uint


def _pair(token_in: Any, token_out: Any) -> tuple:
    """A route's two tokens as typed by the caller, through ``to_address`` (rules ``tokenIn``, ``tokenOut``)."""
    return to_address(token_in, "tokenIn"), to_address(token_out, "tokenOut")


def _to_hop(h) -> Dict[str, Any]:
    return {
        "kind": int(h[0]), "pool": h[1], "token_out": h[2], "currency0": h[3], "currency1": h[4],
        "fee": int(h[5]), "tick_spacing": int(h[6]), "hooks": h[7],
    }


class HopExecutorModule(BaseModule):
    """Reads of the hop executor: routes, caps, quotes, WETH and the hop fee."""

    @property
    def deployed(self) -> bool:
        """True when the address book has a hop executor for this chain."""
        return not is_zero_address(self.client.addresses["hopExecutor"])

    @property
    def address(self) -> str:
        return self._require(self.client.addresses["hopExecutor"], "HopExecutor")

    @property
    def _exec(self):
        return self._contract(self.address, "HopExecutor")

    def quote_hop(self, token_in: str, token_out: str, amount_in: int) -> int:
        """What the route pays for ``amount_in`` (raw ``token_in``, a whole number) right now, raw units of
        ``token_out``, **net of the hop fee** (``fee_ppm``): what a swap gets back. Sent as an eth_call (a simulation,
        no tx). Reverts (decoded into ``BasisContractError``):
          - "no route": there is no route for token_in -> token_out;
          - "above cap": ``amount_in > max_in``, or the exchange's output before the fee ``> max_out``. Both are the full
            caps (``get_caps``): this quote counts no usage, so on Robinhood Chain it can pass while a swap sent now
            reverts on what is left of the window (``check_caps`` counts it);
          - "partial fill": the exchange cannot take the whole input (thin liquidity / price limit);
          - "hook not allowed": a v4 hop's hook is no longer allowed;
          - "no quote": the exchange returned nothing to quote."""
        amount = to_uint(amount_in, "amountIn")
        a, b = _pair(token_in, token_out)
        c = self._exec
        tx = {"from": self.client.account.address} if self.client.account else {}
        try:
            return c.functions.quoteHop(a, b, amount).call(tx)
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="HopExecutor", function_name="quoteHop", abi=c.abi) from e

    def get_route(self, token_in: str, token_out: str) -> List[Dict[str, Any]]:
        """The hops of the route token_in -> token_out (empty when there is none)."""
        hops = self._exec.functions.getRoute(*_pair(token_in, token_out)).call()
        return [_to_hop(h) for h in hops]

    def get_caps(self, token_in: str, token_out: str) -> Dict[str, int]:
        """The route's caps (raw units): {max_in, max_out}, the most one contract block may take in / pay out
        (``max_out`` is before the fee). On Robinhood Chain a contract block is a window of about 12 s, and a swap sent
        now meets what is left of it (``caps_left``)."""
        max_in, max_out = self._exec.functions.getCaps(*_pair(token_in, token_out)).call()
        return {"max_in": max_in, "max_out": max_out}

    def caps_left(self, token_in: str, token_out: str) -> Dict[str, int]:
        """What the route has left now: the caps minus what hops used in the current contract block, raw units:
        {in_left, out_left}. On Robinhood Chain (a window of about 12 s) this is what a swap sent now meets. On a chain
        whose contract block is its own it describes the latest mined block, and a swap sent now starts from the full
        caps (``get_caps``)."""
        in_left, out_left = self._exec.functions.capsLeft(*_pair(token_in, token_out)).call()
        return {"in_left": in_left, "out_left": out_left}

    def check_caps(self, token_in: str, token_out: str, amount_in: int,
                   gross_out: Optional[int] = None, executor: Optional[str] = None) -> Dict[str, Any]:
        """Whether ``amount_in`` (raw ``token_in``) and, optionally, ``gross_out`` (raw ``token_out`` the exchange pays
        before the fee) fit the route's caps the way a swap sent now is checked, counting usage as the executor does:
        against ``caps_left`` where the contract block is the parent chain's (``chain_config.contract_block_is_l1``:
        Robinhood Chain, a window of about 12 s), against the full caps (``get_caps``) where it is the chain's own
        (a local node: a swap sent now lands in a later block, whose usage starts at 0). ``executor`` reads another executor
        than the address book's (``trading.quote_swap`` passes the one trades use).

        Returns {fits (the swap fits what it meets now: ``amount_in`` and, when given, ``gross_out`` within caps_left on
        Robinhood Chain, within the full caps on a chain whose contract block is its own), blocked ('above cap' when it
        does not fit, else None), caps {max_in, max_out}, caps_left {in_left, out_left}, busy (fits the full caps but not
        what is left now: other hops used part of them; on Robinhood Chain the swap is blocked until the window turns
        over, wait about 12 seconds; elsewhere a swap in a later block is usually fine, information only)}."""
        x = to_uint(amount_in, "amountIn")
        out = None if gross_out is None else to_uint(gross_out, "grossOut")
        a, b = _pair(token_in, token_out)
        c = self._exec if executor is None else self._contract(to_address(executor, "executor"), "HopExecutor")
        f = c.functions
        max_in, max_out = f.getCaps(a, b).call()
        in_left, out_left = f.capsLeft(a, b).call()

        def within(in_cap: int, out_cap: int) -> bool:
            return x <= in_cap and (out is None or out <= out_cap)

        full, left = within(max_in, max_out), within(in_left, out_left)
        fits = left if getattr(self.client.chain_config, "contract_block_is_l1", False) else full
        return {"fits": fits, "blocked": None if fits else "above cap", "caps": {"max_in": max_in, "max_out": max_out},
                "caps_left": {"in_left": in_left, "out_left": out_left}, "busy": full and not left}

    def route_count(self) -> int:
        """Number of routes."""
        return self._exec.functions.routeCount().call()

    def route_at(self, i: int) -> Dict[str, Any]:
        """The i-th route with its hops and caps (the order can change). ``i`` a whole number."""
        token_in, token_out, hops, max_in, max_out = self._exec.functions.routeAt(to_uint(i, "index")).call()
        return {"token_in": token_in, "token_out": token_out, "hops": [_to_hop(h) for h in hops],
                "max_in": max_in, "max_out": max_out}

    def list_routes(self, with_caps_left: bool = False) -> List[Dict[str, Any]]:
        """Every route (``route_count`` + ``route_at``). ``with_caps_left`` adds each route's ``in_left`` /
        ``out_left`` now (``caps_left``, None when that read fails): read fresh, never cached. On Robinhood Chain they
        are what a swap sent now meets; on a chain whose contract block is its own, the full caps ``max_in`` /
        ``max_out`` are (``check_caps`` picks the right one)."""
        with_caps_left = to_bool(with_caps_left, "withCapsLeft", False)
        routes = [self.route_at(i) for i in range(self.route_count())]
        if with_caps_left:
            for r in routes:
                try:
                    left = self.caps_left(r["token_in"], r["token_out"])
                    r["in_left"], r["out_left"] = left["in_left"], left["out_left"]
                except Exception:  # noqa: BLE001
                    r["in_left"] = r["out_left"] = None
        return routes

    def route_key(self, token_in: str, token_out: str) -> str:
        """The key of the route token_in -> token_out (0x-hex)."""
        return Web3.to_hex(self._exec.functions.routeKey(*_pair(token_in, token_out)).call())

    def weth(self) -> str:
        """This chain's wrapped native token (WETH) as the executor knows it."""
        return self._exec.functions.weth().call()

    def fee_ppm(self) -> int:
        """The hop fee on every hop's output, read live, parts per million (at most ``max_fee_ppm()``).
        Quotes are net of it."""
        return self._exec.functions.feePpm().call()

    def max_fee_ppm(self) -> int:
        """The highest ``fee_ppm`` can ever be, parts per million."""
        return self._exec.functions.MAX_FEE_PPM().call()
