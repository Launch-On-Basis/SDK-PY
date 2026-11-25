"""Quotes for leverage and loans (the same figures a real open gets), plus prices and values. All views. TypeScript SDK:
src/modules/LeverageSimulator.ts.

Result dicts (snake_case keys, raw ints; pair asset amounts raw):
  leverage result (``simulate_leverage(_factory)_locked`` / ``simulate``): total_repay, total_borrowed, total_collateral,
    total_fees (trade fees and loan fees), real_liquidity, xe_added, main_pair_added, token_added, total_dex_tax.
  loan result: collateral_value, total_fee (the loan fee for the chosen days), borrowed_amount (what the loan pays out).
"""

from typing import Any, Dict, List, Optional, Sequence, Union

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.leverage_raw import LeverageRaw
from ..core.mains import MainInfo, main_amount, pair_amount
from ..core.units import Amount, to_address, to_uint

Number = Union[int, float]

_END_FIELDS = ("total_repay", "total_borrowed", "total_collateral", "total_fees", "real_liquidity", "xe_added",
               "main_pair_added", "token_added", "total_dex_tax")
_LOAN_FIELDS = ("collateral_value", "total_fee", "borrowed_amount")


def _eq(a: str, b: str) -> bool:
    return a.lower() == b.lower()


def _check_pct(v: Any) -> int:
    """``leverage_pct`` must be a whole number 1-100 (rule "Range")."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or (isinstance(v, float) and not v.is_integer()) \
            or not 1 <= v <= 100:
        raise ValidationError("Range", f"leveragePct must be a whole number 1-100, got {v}.")
    return int(v)


def _to_path(path: Sequence[str]) -> List[str]:
    """A caller's path, every token through ``to_address`` (rule ``path``), checksummed."""
    return [to_address(t, "path") for t in path]


def _end_result(r: Dict[str, int]) -> Dict[str, int]:
    return {k: r[k] for k in _END_FIELDS}


def _loan_result(r: Dict[str, int]) -> Dict[str, int]:
    return {k: r[k] for k in _LOAN_FIELDS}


class LeverageSimulatorModule(BaseModule):
    """Quotes for leverage and loans (the same figures a real open gets). All views."""

    def __init__(self, client):
        super().__init__(client)
        self._raw = LeverageRaw(client)

    @property
    def deployed(self) -> bool:
        """True when the leverage simulator is deployed on this chain."""
        return not is_zero_address(self.client.addresses["leverage"])

    @property
    def _leverage(self):
        return self._contract(self._require(self.client.addresses["leverage"], "Leverage"), "Leverage")


    def simulate_leverage_locked(self, amount: int, path: Sequence[str], number_of_days: Number,
                                 leverage_pct: Number = 100, trader: Optional[str] = None) -> Dict[str, int]:
        """Leverage on a main, path ``[pairAsset, main]``. ``amount`` raw pair asset. ``leverage_pct`` (1-100): 100
        quotes ``trading.leverage_buy``, 1-99 quotes ``trading.mixed_buy`` (lower keeps more of the position as
        debt-free collateral). Priced at the rate of ``trader`` (default the signer; with neither, the list rate with no
        wallet discounts). This is the exact quote the open gets when ``trader`` is the wallet opening. On an add to an
        open position, pass the days left (``trading.quote_leverage`` does)."""
        if len(path) != 2:
            raise ValidationError("Path", f"simulateLeverageLocked takes [pairAsset, main] (2 hops), got {len(path)}; "
                                          "use simulateLeverageFactoryLocked for a factory token.")
        pct = _check_pct(leverage_pct)
        path = _to_path(path)
        who = to_address(trader, "trader") if trader is not None else None
        self._check_leverage_path(path)
        return _end_result(self._raw.simulate_leverage(amount, path, number_of_days, pct, who))

    def simulate_leverage_factory_locked(self, amount: int, path: Sequence[str], number_of_days: Number,
                                         leverage_pct: Number = 100, trader: Optional[str] = None) -> Dict[str, int]:
        """``simulate_leverage_locked`` for a factory token, path ``[pairAsset, main, token]``. ``amount`` raw pair
        asset."""
        if len(path) != 3:
            raise ValidationError("Path", f"simulateLeverageFactoryLocked takes [pairAsset, main, token] (3 hops), "
                                          f"got {len(path)}.")
        pct = _check_pct(leverage_pct)
        path = _to_path(path)
        who = to_address(trader, "trader") if trader is not None else None
        self._check_leverage_path(path)
        return _end_result(self._raw.simulate_leverage(amount, path, number_of_days, pct, who))

    def simulate(self, amount: int, path: Sequence[str], number_of_days: Number,
                 leverage_pct: Number = 100, trader: Optional[str] = None) -> Dict[str, int]:
        """``simulate_leverage_locked`` (path of 2) or ``simulate_leverage_factory_locked`` (path of 3), picked by path
        length. ``leverage_pct`` 100 = plain leverage."""
        if len(path) == 3:
            return self.simulate_leverage_factory_locked(amount, path, number_of_days, leverage_pct, trader)
        return self.simulate_leverage_locked(amount, path, number_of_days, leverage_pct, trader)

    def simulate_loan_for(self, main: Optional[str], bought_tokens: int, number_of_days: Number) -> Dict[str, int]:
        """A loan on ``bought_tokens`` main tokens at ``main``'s current price (``main`` None: the default main)."""
        info = self._main_info(main)
        r0, r1 = self._raw.curve_reserves(info.main, True)
        return _loan_result(self._raw.simulate_loan(info.main, bought_tokens, r0, r1, number_of_days))

    def simulate_loan_hybrid_for(self, token: str, bought_tokens: int, number_of_days: Number) -> Dict[str, int]:
        """A loan on ``bought_tokens`` factory tokens, valued at the token's current floor."""
        token = to_address(token, "token")
        return _loan_result(self._raw.simulate_loan_hybrid(bought_tokens, self._raw.simulation_state_for(token),
                                                           number_of_days))


    def get_price_multiplier(self) -> int:
        """The scale of prices in main per token (1e18)."""
        return self._leverage.functions.PRICEMULTIPLIER().call()

    def calculate_floor_for(self, token: str) -> Amount:
        """A factory token's current floor, in pair asset per token. The floor is measured in the pair asset, which can
        move itself."""
        token = to_address(token, "token")
        st = self._raw.simulation_state_for(token)
        info = self.client.mains.info_of_token(token)
        raw = self._raw.calculate_floor({
            "reversed": st["reversed"], "hybrid_multiplier": st["multiplier"], "reserve0": st["reserve0"],
            "reserve1": st["reserve1"], "base_reserve0": st["basereserve0"], "base_reserve1": st["basereserve1"],
            "xereserve0": st["xereserve0"], "xereserve1": st["xereserve1"],
        })
        return pair_amount(raw, info)

    def get_token_price_for(self, token: str) -> Amount:
        """Current price: a main's in pair asset, a factory token's in main."""
        token = to_address(token, "token")
        is_main = self.client.mains.is_main(token)
        r0, r1 = self._raw.curve_reserves(token, is_main)
        info = self.client.mains.info_of_token(token)
        raw = self._raw.get_token_price(r0, r1)
        return pair_amount(raw, info) if is_main else main_amount(raw, info)

    def get_pair_price_for(self, token: str) -> Amount:
        """Current price in pair asset, for a factory token or a main."""
        token = to_address(token, "token")
        info = self.client.mains.info_of_token(token)
        if _eq(info.main, token):
            r0, r1 = self._raw.curve_reserves(token, True)
            return pair_amount(self._raw.get_token_price(r0, r1), info)
        r0, r1 = self._raw.curve_reserves(token, False)
        x0, x1 = self._raw.curve_reserves(info.main, True)
        return pair_amount(self._raw.get_pair_price(r0, r1, x0, x1), info)

    def get_collateral_value_for(self, token: str, token_amount: int) -> Amount:
        """Collateral value of ``token_amount`` (raw, 18 dec) now, in pair asset. A factory token is valued at its
        floor."""
        to_uint(token_amount, "tokenAmount")
        token = to_address(token, "token")
        info = self.client.mains.info_of_token(token)
        if _eq(info.main, token):
            r0, r1 = self._raw.curve_reserves(token, True)
            return pair_amount(self._raw.get_collateral_value(token_amount, r0, r1), info)
        return pair_amount(self._raw.get_collateral_value_hybrid(token_amount, self._raw.simulation_state_for(token)), info)


    def _check_leverage_path(self, path: Sequence[str]) -> None:
        """Checks the path before simulating: ``path[1]`` must be a main, ``path[0]`` its pair asset and ``path[2]`` (if
        any) a listed factory token on that main (a delisted token cannot be leveraged). Rule ``Path``. An RPC failure of
        a read is raised as it came, never as a path refusal."""
        p0, main = path[0], path[1]
        token = path[2] if len(path) > 2 else None
        if not self.client.mains.is_main(main):
            raise ValidationError("Path", f"path[1] {main} is not a main.")
        info = self.client.mains.get(main)
        if not _eq(p0, info.pair_asset):
            raise ValidationError("Path", f"path[0] must be {info.pair_symbol} ({info.pair_asset}), the pair asset of "
                                          f"{main}; got {p0}.")
        if token is None:
            return
        if self.client.mains.is_main(token):
            raise ValidationError("Path", f"path[2] {token} is a main, not a factory token.")
        if not self.client.factory.is_ecosystem_token(token):
            raise ValidationError("Path", f"path[2] {token} is not a listed factory token: leverage is open on listed "
                                          "tokens only.")
        try:
            quote = self.client.mains.main_of(token)
        except ValidationError:
            raise ValidationError("Path", f"path[2] {token} is not a factory token.") from None
        if not _eq(quote, main):
            raise ValidationError("Path", f"path[2] {token} trades against {quote}, not {main}.")

    def _main_info(self, main: Optional[str] = None) -> MainInfo:
        return self.client.mains.default() if main is None else self.client.mains.get(main)
