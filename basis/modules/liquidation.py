"""Liquidation views. All reads. TypeScript SDK: src/modules/Liquidation.ts.

A loan or leverage position can be liquidated once its deadline has passed. Nothing on a main is liquidated while its pair
is paused, or while the issuer of its pair asset has paused that token (USDG and tokenized stocks can be paused). While
the pair is paused you can still repay and claim, but not extend. While the issuer has paused the pair asset you can
neither repay nor extend. A loan whose deadline passed meanwhile can still be repaid or extended once the pair reopens. A
``client.loans`` loan is addressed with ``user`` = the loan hub (``client.addresses["loanHub"]``) and ``loan_id`` = its
id on the main (``loans.get_loan_ref(...)["core_loan_id"]``; ``loans.core_loan_owner(main, core_id)`` maps back to the
wallet). A vault position's loan maps back to the vault.

After a liquidation, part of the collateral left over is kept as a liquidation fee and the rest is yours. You have a year
to claim it: a leftover still unclaimed 365 days after the liquidation can be taken, after which the claim answers
"Nothing to claim".
- hub loan: ``loans.claim_liquidation(hub_id)``;
- leverage position: ``trading.claim_leverage_liquidation(main, loan_id)``;
- vault position: settled inside the liquidation, back into your stake; nothing to call;
- vesting loan: settled by ``vesting.repay_loan_on_vesting``.

Claim status (``get_claim_status``): is_liquidated, claimable (raw collateral units; 0 once claimed, or once taken after the
deadline), liquidated_at (unix seconds, 0 when not liquidated), claim_deadline (liquidated_at + 365 days; claim before it),
claim_expired (the deadline passed with something still unclaimed: it can be taken at any time now). Expired-claim lookup
(``get_expired_claim``): status ('taken': the leftover went unclaimed past its deadline and is gone; 'notTaken': it was not,
for sure, because that can't have happened on this loan or the scan reached block 0; 'notInWindow': nothing in the blocks
scanned, which does NOT mean nothing happened, so widen ``from_block``), claim ({amount, tx_hash, block_number} or None),
from_block, to_block (the blocks scanned; None when it can't have happened and no logs were read).
"""

from typing import Any, Dict, Optional, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.loans import get_partial_loan_sell_amounts, is_asset_paused, read_core_loan
from ..core.units import opt_uint, to_address, to_bool, to_uint

Number = Union[int, float]

CLAIM_PERIOD_SECONDS = 365 * 86_400
EXPIRED_CLAIM_SCAN_BLOCKS = 100_000


def _is_int(v: Any) -> bool:
    """JS ``Number.isInteger``: an int (not bool) or a whole float."""
    if isinstance(v, bool):
        return False
    return isinstance(v, int) or (isinstance(v, float) and v.is_integer())


class LiquidationModule(BaseModule):
    """Liquidation views."""

    @property
    def deployed(self) -> bool:
        """True when liquidations are set up on this chain."""
        return not is_zero_address(self.client.addresses["liquidator"])

    def is_liquidatable(self, main: str, user: str, loan_id: int, is_leverage: bool) -> bool:
        """True when loan (or, with ``is_leverage``, leverage position) ``loan_id`` of ``user`` on ``main`` can be
        liquidated now: it is open, past its deadline by chain time, and nothing blocks liquidations on the main
        (``get_liquidation_block``)."""
        loan = self.get_loan(main, user, loan_id, is_leverage)
        if not loan["active"]:
            return False
        now = self.web3.eth.get_block("latest")["timestamp"]
        if loan["liquidation_time"] >= now:
            return False
        return self.get_liquidation_block(main) is None

    def get_liquidation_block(self, main: str) -> Optional[str]:
        """Why nothing on ``main`` can be liquidated right now, or None when it can: 'no liquidator' (liquidations are
        not set up for this main), 'pair not active' (the pair is paused) or 'pair asset paused' (the asset's issuer
        paused it; a token without ``paused()``, or one answering anything but true, counts as live)."""
        info = self.client.mains.get(main)
        swap = self._contract(self._require(self.client.addresses["swap"], "Swap"), "Swap")
        liquidator = self._liquidator_of(info.main)
        active = swap.functions.isMain(Web3.to_checksum_address(info.main)).call()
        paused = is_asset_paused(self.client, info.pair_asset)
        if is_zero_address(liquidator):
            return "no liquidator"
        if not active:
            return "pair not active"
        return "pair asset paused" if paused else None

    def get_loan(self, main: str, user: str, loan_id: int, is_leverage: bool) -> Dict[str, Any]:
        """Loan (or, with ``is_leverage``, leverage position) ``loan_id`` of ``user`` on ``main`` (a ``client.loans``
        loan: see the module docstring); keys as in ``core/loans.py``."""
        return read_core_loan(self.client, self.client.mains.address_of(main), to_address(user, "user"),
                              to_uint(loan_id, "loanId"), to_bool(is_leverage, "isLeverage"))

    def get_claim_status(self, main: str, user: str, loan_id: int, is_leverage: bool) -> Dict[str, Any]:
        """The owner's liquidation leftover of loan or leverage position ``loan_id`` of ``user`` on ``main`` (a
        ``client.loans`` loan: see the module docstring): what is claimable, when it was liquidated, the claim deadline
        and whether it passed."""
        loan = self.get_loan(main, user, loan_id, is_leverage)
        now = self.web3.eth.get_block("latest")["timestamp"]
        liq = loan["is_liquidated"]
        claim_deadline = loan["time_of_closure"] + CLAIM_PERIOD_SECONDS if liq else 0
        return {
            "is_liquidated": liq, "claimable": loan["liquidation_claim"],
            "liquidated_at": loan["time_of_closure"] if liq else 0, "claim_deadline": claim_deadline,
            "claim_expired": bool(liq and loan["liquidation_claim"] > 0 and now > claim_deadline),
        }

    def is_claim_expired(self, main: str, user: str, loan_id: int, is_leverage: bool) -> bool:
        """True when the leftover of loan or leverage position ``loan_id`` of ``user`` on ``main`` is past its claim
        deadline (see ``get_claim_status``)."""
        return self.get_claim_status(main, user, loan_id, is_leverage)["claim_expired"]

    def get_expired_claim(self, main: str, user: str, loan_id: int, is_leverage: bool,
                          from_block: Optional[int] = None) -> Dict[str, Any]:
        """Looks for a leftover of loan or leverage position ``loan_id`` of ``user`` on ``main`` that went unclaimed past
        its deadline and was taken: the expired-claim dict (module docstring). Reads the loan first and answers 'notTaken' without a
        log scan when that can't have happened (not liquidated, still claimable, or not yet 365 days since the
        liquidation). Otherwise it scans the last ``EXPIRED_CLAIM_SCAN_BLOCKS`` blocks, or from ``from_block`` to widen
        that (a whole-chain scan at 10 blocks a second is more than an RPC will serve). Finding nothing there is
        'notInWindow', not proof: only a scan from block 0 answers 'notTaken'."""
        lid = to_uint(loan_id, "loanId")
        is_leverage = to_bool(is_leverage, "isLeverage")
        explicit = opt_uint(from_block, "fromBlock")
        user = to_address(user, "user")
        loan = self.get_loan(main, user, lid, is_leverage)
        blk = self.web3.eth.get_block("latest")
        if (not loan["is_liquidated"] or loan["liquidation_claim"] > 0
                or blk["timestamp"] <= loan["time_of_closure"] + CLAIM_PERIOD_SECONDS):
            return {"status": "notTaken", "claim": None, "from_block": None, "to_block": None}
        to_block = blk["number"]
        start = max(to_block - EXPIRED_CLAIM_SCAN_BLOCKS + 1, 0) if explicit is None else explicit
        if start > to_block:
            raise ValidationError("fromBlock", f"fromBlock {start} is after the latest block {to_block}.")
        addr = self.client.mains.address_of(main)
        c = self._contract(addr, "MainToken")
        abi = next(e for e in c.abi if e.get("type") == "event" and e.get("name") == "ClaimSeized")
        topic0 = Web3.to_hex(Web3.keccak(text=f"ClaimSeized({','.join(i['type'] for i in abi['inputs'])})"))
        user_topic = "0x" + user[2:].lower().rjust(64, "0")
        raw = self.web3.eth.get_logs({
            "address": Web3.to_checksum_address(addr), "fromBlock": start, "toBlock": to_block,
            "topics": [topic0, user_topic, "0x" + format(lid, "064x")],
        })
        ev = c.events.ClaimSeized()
        logs = [ev.process_log(lg) for lg in raw]
        log = next((lg for lg in logs if lg["args"]["isLeverage"] == is_leverage), None)
        if log is not None:
            claim = {"amount": log["args"]["amount"], "tx_hash": Web3.to_hex(log["transactionHash"]),
                     "block_number": log["blockNumber"]}
            return {"status": "taken", "claim": claim, "from_block": start, "to_block": to_block}
        return {"status": "notTaken" if start == 0 else "notInWindow", "claim": None, "from_block": start,
                "to_block": to_block}

    def get_partial_loan_sell_amounts(self, main: str, loan_id: int, user: str, percentage: Number,
                                      is_leverage: bool) -> Dict[str, int]:
        """What a partial sell of ``percentage`` (1-100; sells themselves take steps of 10) of loan or leverage position
        ``loan_id`` of ``user`` on ``main`` would use up and leave to sell: {main_used, tokens_used, residual_tokens}.
        For a ``client.loans`` loan see the module docstring."""
        if not _is_int(percentage) or percentage < 1 or percentage > 100:
            raise ValidationError("Invalid percentage", f"percentage must be 1-100, got {percentage}.")
        return get_partial_loan_sell_amounts(self.client, self.client.mains.address_of(main),
                                             to_uint(loan_id, "loanId"), to_address(user, "user"), int(percentage),
                                             to_bool(is_leverage, "isLeverage"))

    def _liquidator_of(self, main: str) -> str:
        """The main's liquidator; zero until it is set."""
        return self._contract(self.client.mains.address_of(main), "MainToken").functions.LIQUIDATOR().call()
