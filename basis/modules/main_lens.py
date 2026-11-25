"""``client.main_lens``: read-only quotes for the mains (every method takes the main first).
TypeScript SDK: src/modules/MainLens.ts.

Amounts raw ints (main tokens 18 dec, pair asset in its own decimals). Reverts are decoded into
``BasisContractError``. ``main`` is an address or an address-book key.
"""

from typing import Dict, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.loans import get_partial_loan_sell_amounts
from ..core.revert import decode_revert
from ..core.units import to_address, to_bool, to_uint

Number = Union[int, float]


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


class MainLensModule(BaseModule):
    """Main lens reads: main/pair conversions, leverage count per token, partial-sell amounts."""

    @property
    def deployed(self) -> bool:
        """True when the main lens is deployed on this chain."""
        return not is_zero_address(self.client.addresses["mainLens"])

    @property
    def address(self) -> str:
        return self._require(self.client.addresses["mainLens"], "MainLens")

    @property
    def _lens(self):
        return self._contract(self.address, "MainLens")

    def calculate_main_pair_for_tokens(self, main: str, token_amount_out: int) -> int:
        """Pair asset in (raw) to buy ``token_amount_out`` main tokens on ``main``, before fees. 0 for 0."""
        fn = self._lens.functions.calculateMainPairForTokens(
            self._main(main), to_uint(token_amount_out, "tokenAmountOut"))
        return self._call(fn, "calculateMainPairForTokens")

    def calculate_tokens_for_main_pair(self, main: str, pair_amount_out: int) -> int:
        """Main tokens in (raw) to sell for ``pair_amount_out`` raw pair asset on ``main``, before fees. 0 for 0."""
        fn = self._lens.functions.calculateTokensForMainPair(self._main(main), to_uint(pair_amount_out, "pairAmountOut"))
        return self._call(fn, "calculateTokensForMainPair")

    def get_leverage_count_for_user_and_token(self, main: str, user: str, token: str) -> int:
        """How many leverage positions ``user`` ever opened on ``token`` on ``main`` (open and closed; at most one is
        open at a time: ``trading.get_open_leverage``)."""
        fn = self._lens.functions.getLeverageCountForUserAndToken(
            self._main(main), to_address(user, "user"), to_address(token, "token"))
        return self._call(fn, "getLeverageCountForUserAndToken")

    def get_partial_loan_sell_amounts(self, main: str, loan_id: int, user: str, percentage: Number,
                                      is_leverage: bool = True) -> Dict[str, int]:
        """What a partial sell of ``percentage`` (1-100) % of leverage position (or, with ``is_leverage`` False, loan)
        ``loan_id`` of ``user`` on ``main`` would use up and leave to sell (raw collateral-token units): {main_used,
        tokens_used, residual_tokens}. A loan that is not open (repaid, sold or never opened) raises
        ValidationError("Loan not active"). Reverts "Invalid percentage" / "Already liquidated" / "cannot sell off
        with no profit" come back as BasisContractError."""
        main_addr, lid = self._main(main), to_uint(loan_id, "loanId")
        pct, lev = to_uint(percentage, "percentage"), to_bool(is_leverage, "isLeverage")
        return get_partial_loan_sell_amounts(self.client, main_addr, lid, to_address(user, "user"), pct, lev)


    def _main(self, main: str) -> str:
        return _cs(self.client.mains.address_of(main))

    def _call(self, fn, name: str):
        try:
            return fn.call()
        except Exception as e:  # noqa: BLE001
            raise decode_revert(e, contract="MainLens", function_name=name, abi=self._lens.abi) from e
