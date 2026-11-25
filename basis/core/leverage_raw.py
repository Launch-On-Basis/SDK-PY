"""Internal: not part of the public package surface. The raw reads behind ``client.leverage_simulator``. TypeScript SDK:
src/core/leverageRaw.ts.

Result dicts (snake_case keys, raw ints):
  end result: new_xe_reserve0, new_xe_reserve1, new_reserve0, new_reserve1, total_repay, total_borrowed, total_collateral,
    total_fees, real_liquidity, xe_added, main_pair_added, token_added, total_dex_tax.
  simulation state: amount, xereserve0, xereserve1, reserve0, reserve1, multiplier,
    reversed, basereserve0, basereserve1, min_amount, main, tax_rate.
  dex result: dex_tax, reserve0, reserve1, reserve0_added, reserve1_added, bought_tokens.
  loan result: collateral_value, total_fee, borrowed_amount, reserve0, reserve1, xereserve0, xereserve1, lp_fee.
  floor inputs (``calculate_floor``): reversed, hybrid_multiplier, reserve0, reserve1, base_reserve0, base_reserve1,
    xereserve0, xereserve1.
"""

from typing import Any, Dict, Optional, Sequence, Tuple, Union

from web3 import Web3

from ..config.addresses import ZERO_ADDRESS
from .base import BaseModule
from .errors import ValidationError
from .units import to_bool, to_uint

Number = Union[int, float]

_END_FIELDS = (
    "new_xe_reserve0", "new_xe_reserve1", "new_reserve0", "new_reserve1", "total_repay", "total_borrowed",
    "total_collateral", "total_fees", "real_liquidity", "xe_added", "main_pair_added", "token_added", "total_dex_tax",
)
_DEX_FIELDS = ("dex_tax", "reserve0", "reserve1", "reserve0_added", "reserve1_added", "bought_tokens")
_LOAN_FIELDS = ("collateral_value", "total_fee", "borrowed_amount", "reserve0", "reserve1", "xereserve0", "xereserve1",
                "lp_fee")


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


def _js(v: Any) -> str:
    """``v`` as TS ``String(v)`` shows it in messages."""
    if v is None:
        return "undefined"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _state_tuple(state: Dict[str, Any]) -> tuple:
    """A SimulationState dict as the web3 struct tuple, every uint checked by ``to_uint`` and ``reversed`` a bool."""
    if not isinstance(state, dict):
        raise ValidationError("state", f"state must be a SimulationState, got {_js(state)}.")
    return (to_uint(state["amount"], "amount"), to_uint(state["xereserve0"], "xereserve0"),
            to_uint(state["xereserve1"], "xereserve1"), to_uint(state["reserve0"], "reserve0"),
            to_uint(state["reserve1"], "reserve1"), to_uint(state["multiplier"], "multiplier"),
            to_bool(state["reversed"], "reversed"), to_uint(state["basereserve0"], "basereserve0"),
            to_uint(state["basereserve1"], "basereserve1"), to_uint(state["min_amount"], "minAmount"), _cs(state["main"]),
            to_uint(state["tax_rate"], "taxRate"))


class LeverageRaw(BaseModule):
    @property
    def _leverage(self):
        return self._contract(self._require(self.client.addresses["leverage"], "Leverage"), "Leverage")

    def trader_of(self, trader: Optional[str] = None) -> str:
        """Whose rate a quote is priced at: ``trader``, else the signer, else the zero address (the list rate)."""
        if trader:
            return _cs(trader)
        return self.client.account.address if self.client.account else ZERO_ADDRESS

    def simulate_leverage(self, amount: int, path: Sequence[str], number_of_days: Number, leverage_pct: int,
                          trader: Optional[str] = None) -> Dict[str, int]:
        """Simulates opening a leverage position: a path of 2 for a main, a path of 3 for a factory token."""
        fns = self._leverage.functions
        call = fns.simulateLeverageFactoryLocked if len(path) == 3 else fns.simulateLeverageLocked
        r = call(to_uint(amount, "amount"), [_cs(p) for p in path], to_uint(number_of_days, "numberOfDays"), leverage_pct,
                 self.trader_of(trader)).call()
        return dict(zip(_END_FIELDS, r))

    def simulate_dex(self, amount: int, tax_rate: int, reserve0: int, reserve1: int) -> Dict[str, int]:
        r = self._leverage.functions.simulateDex(
            to_uint(amount, "amount"), to_uint(tax_rate, "taxRate"), to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1")).call()
        return dict(zip(_DEX_FIELDS, r))

    def simulate_dex_factory(self, amount: int, reserve0: int, reserve1: int, multiplier: Number,
                             reversed: bool) -> Dict[str, int]:
        r = self._leverage.functions.simulateDexFactory(
            to_uint(amount, "amount"), to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1"),
            to_uint(multiplier, "multiplier"), to_bool(reversed, "reversed")).call()
        return dict(zip(_DEX_FIELDS, r))

    def simulate_loan(self, main: str, bought_tokens: int, reserve0: int, reserve1: int,
                      number_of_days: Number) -> Dict[str, int]:
        r = self._leverage.functions.simulateLoan(
            _cs(main), to_uint(bought_tokens, "boughtTokens"), to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1"),
            to_uint(number_of_days, "numberOfDays")).call()
        return dict(zip(_LOAN_FIELDS, r))

    def simulate_loan_hybrid(self, bought_tokens: int, state: Dict[str, Any], number_of_days: Number) -> Dict[str, int]:
        r = self._leverage.functions.simulateLoanHybrid(
            to_uint(bought_tokens, "boughtTokens"), _state_tuple(state), to_uint(number_of_days, "numberOfDays")).call()
        return dict(zip(_LOAN_FIELDS, r))

    def calculate_tokens_for_buy(self, main_pair_amount: int, reserve0: int, reserve1: int) -> int:
        return self._leverage.functions.calculateTokensForBuy(
            to_uint(main_pair_amount, "mainPairAmount"), to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1")).call()

    def calculate_floor(self, p: Dict[str, Any]) -> int:
        return self._leverage.functions.calculateFloor(
            to_bool(p["reversed"], "reversed"), to_uint(p["hybrid_multiplier"], "hybridMultiplier"),
            to_uint(p["reserve0"], "reserve0"), to_uint(p["reserve1"], "reserve1"), to_uint(p["base_reserve0"], "baseReserve0"),
            to_uint(p["base_reserve1"], "baseReserve1"), to_uint(p["xereserve0"], "xereserve0"),
            to_uint(p["xereserve1"], "xereserve1")).call()

    def get_token_price(self, reserve0: int, reserve1: int) -> int:
        return self._leverage.functions.getTokenPrice(to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1")).call()

    def get_pair_price(self, reserve0: int, reserve1: int, xereserve0: int, xereserve1: int) -> int:
        return self._leverage.functions.getPairPrice(
            to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1"), to_uint(xereserve0, "xereserve0"),
            to_uint(xereserve1, "xereserve1")).call()

    def get_collateral_value(self, token_amount: int, reserve0: int, reserve1: int) -> int:
        return self._leverage.functions.getColleteralValue(
            to_uint(token_amount, "tokenAmount"), to_uint(reserve0, "reserve0"), to_uint(reserve1, "reserve1")).call()

    def get_collateral_value_hybrid(self, token_amount: int, state: Dict[str, Any]) -> int:
        return self._leverage.functions.getColleteralValueHybrid(to_uint(token_amount, "tokenAmount"), _state_tuple(state)).call()

    def simulation_state_for(self, token: str, amount: int = 0, trader: Optional[str] = None) -> Dict[str, Any]:
        """The SimulationState of a factory token as it is now. ``amount`` defaults to 0."""
        main = self._factory_main_of(token)
        reserve0, reserve1 = self.curve_reserves(token, False)
        xereserve0, xereserve1 = self.curve_reserves(main, True)
        t = self._contract(token, "FactoryToken").functions
        multiplier = t.hybridMultiplier().call()
        reversed_ = t.reversed().call()
        basereserve0 = t.baseReserve0().call()
        basereserve1 = t.baseReserve1().call()
        main_base1 = self._contract(main, "MainToken").functions.baseReserve1().call()
        m = main_base1 // 10_000
        tax_rate = self._contract(self.client.addresses["taxes"], "Taxes").functions.getTaxRate(
            _cs(token), self.trader_of(trader)).call()
        return {
            "amount": to_uint(amount, "amount"), "xereserve0": xereserve0, "xereserve1": xereserve1,
            "reserve0": reserve0, "reserve1": reserve1, "multiplier": int(multiplier), "reversed": reversed_,
            "basereserve0": basereserve0, "basereserve1": basereserve1, "min_amount": 1 if m == 0 else m, "main": main,
            "tax_rate": tax_rate,
        }

    def read_curve(self, token_or_main: str) -> Dict[str, int]:
        """The reserves of a factory token or a main: {'reserve0', 'reserve1', 'block_timestamp_last'}."""
        addr = _cs(token_or_main)
        c = self._contract(addr, "MainToken" if self.client.mains.is_main(addr) else "FactoryToken")
        reserve0, reserve1, ts = c.functions.getCurveReserves().call()
        return {"reserve0": reserve0, "reserve1": reserve1, "block_timestamp_last": ts}

    def curve_reserves(self, token: str, is_main: bool) -> Tuple[int, int]:
        """The reserves of ``token`` when the caller already knows whether it is a main."""
        c = self._contract(token, "MainToken" if is_main else "FactoryToken")
        r = c.functions.getCurveReserves().call()
        return r[0], r[1]

    def _factory_main_of(self, token: str) -> str:
        """The main of a factory token; a ValidationError for a main."""
        main = self.client.mains.main_of(token)
        if main.lower() == token.lower():
            raise ValidationError("FactoryToken", f"{token} is a main, not a factory token; use the main variants "
                                                  "(get_collateral_value_for / simulate_loan_for).")
        return main
