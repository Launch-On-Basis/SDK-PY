"""Trade fees: the base rate, dev-set creator / rewards fees, dev share wallets and referral earnings (who referred
whom is ``client.referrals``; TypeScript SDK: src/modules/Taxes.ts). Fees are read live, in bps. Earnings are per pair asset
and returned as ``Amount`` in pair-asset units.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import ValidationError
from ..core.units import Amount, to_address, to_amount, to_bool, to_uint

TOTAL_BASIS_POINTS = 10_000
MAX_SHARES = 10


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


class TaxesModule(BaseModule):
    """Trade fees: the base rate, dev-set creator / rewards fees, dev share wallets and referral earnings."""

    @property
    def deployed(self) -> bool:
        """Whether the ``taxes`` address is set on this chain."""
        return not is_zero_address(self.client.addresses["taxes"])

    @property
    def _address(self) -> str:
        return self._require(self.client.addresses["taxes"], "Taxes")

    @property
    def _taxes(self):
        return self._contract(self._address, "Taxes")


    def get_base_rate(self) -> int:
        """The base fee in bps, read live: before the referral discount and without a token's creator / rewards fees
        (the rate loan actions pay)."""
        return int(self._taxes.functions.baseRate().call())

    def get_tax_rate_base(self, token: str, user: str, base_bps: Union[int, float]) -> int:
        """The fee ``user`` pays on ``token``'s side of a trade, in bps, for an explicit base ``base_bps``: the base
        (lower when ``user`` has a referrer) plus the token's creator and rewards fees (mains: base only); 0 for a
        fee-whitelisted wallet. For quoting a side that charges a different base, such as one leg of a cross-pair swap;
        ``get_tax_rate`` is this with the live base rate."""
        token, user = to_address(token, "token"), to_address(user, "user")
        bps = to_uint(base_bps, "baseBps")
        return int(self._taxes.functions.getTaxRateBase(token, user, bps).call())

    def get_tax_rate(self, token: str, user: str) -> int:
        """The fee ``user`` pays trading ``token``, in bps, read live: the base rate (lower when ``user`` has a
        referrer) plus the token's creator and rewards fees (mains: base only). 0 for a fee-whitelisted wallet."""
        token, user = to_address(token, "token"), to_address(user, "user")
        return int(self._taxes.functions.getTaxRate(token, user).call())

    def get_token_tax_info(self, token: str) -> Dict[str, Any]:
        """Every fee setting of a token in one call. Rates in bps of the trade.

        Keys: is_main (a main, paused or not; mains pay the base rate only), hybrid_multiplier (100 for a main; the
        reversed setting on a reversed token), rewards_enabled, dev, base_rate, creator_fee, rewards_fee,
        total_rate (base + creator + rewards for an unreferred, non-whitelisted wallet), max_creator_fee
        (0 for a main), max_rewards_fee (0 for a main or a token without rewards), reversed.
        """
        i = self._taxes.functions.getTokenTaxInfo(to_address(token, "token")).call()
        return {
            "is_main": bool(i[0]),
            "hybrid_multiplier": int(i[1]),
            "rewards_enabled": bool(i[2]),
            "dev": i[3],
            "base_rate": int(i[4]),
            "creator_fee": int(i[5]),
            "rewards_fee": int(i[6]),
            "total_rate": int(i[7]),
            "max_creator_fee": int(i[8]),
            "max_rewards_fee": int(i[9]),
            "reversed": bool(i[10]),
        }

    def get_max_creator_fee(self) -> int:
        """The creator-fee cap in bps, read live."""
        return int(self._taxes.functions.MAX_CREATOR_FEE().call())

    def get_max_rewards_fee(self, hybrid_multiplier: Union[int, float], reversed: bool) -> int:
        """The rewards-fee cap in bps for a token with these settings, read live."""
        return int(self._taxes.functions.maxRewardsFee(to_uint(hybrid_multiplier, "hybridMultiplier"), to_bool(reversed, "reversed")).call())

    def is_whitelisted(self, wallet: str) -> bool:
        """True when ``wallet`` is fee-whitelisted (pays no trade fee)."""
        return bool(self._taxes.functions.isWhitelisted(to_address(wallet, "wallet")).call())

    def get_fee_cooldowns(self, token: str) -> Dict[str, Any]:
        """Cooldown settings and the last change of ``token``'s creator and rewards fees, and whether each can change
        now (against the latest block's timestamp). Times are unix seconds.

        Keys: creator_fee_cooldown / rewards_fee_cooldown (seconds between changes, 0 = none),
        creator_fee_set_at / rewards_fee_set_at (0 = never set; the first setting is free),
        creator_fee_next_change_at / rewards_fee_next_change_at (``set_at + cooldown``, or 0 before the first setting;
        a time in the past also means "can change now": it stays a past timestamp once the cooldown ran out),
        can_change_creator_fee / can_change_rewards_fee (bool: the fee can be changed now).
        """
        t = to_address(token, "token")
        fns = self._taxes.functions
        cc = fns.creatorFeeCooldown().call()
        rc = fns.rewardsFeeCooldown().call()
        c_at = fns.creatorFeeSetAt(t).call()
        r_at = fns.rewardsFeeSetAt(t).call()
        now = self.web3.eth.get_block("latest")["timestamp"]
        creator_next = 0 if c_at == 0 else int(c_at + cc)
        rewards_next = 0 if r_at == 0 else int(r_at + rc)
        return {
            "creator_fee_cooldown": int(cc),
            "rewards_fee_cooldown": int(rc),
            "creator_fee_set_at": int(c_at),
            "rewards_fee_set_at": int(r_at),
            "creator_fee_next_change_at": creator_next,
            "rewards_fee_next_change_at": rewards_next,
            "can_change_creator_fee": now >= creator_next,
            "can_change_rewards_fee": now >= rewards_next,
        }


    def set_creator_fee(self, token: str, bps: Union[int, float]) -> Dict[str, Any]:
        """Set ``token``'s creator fee (bps on top of the base rate, paid to the dev and share wallets).
        Caller: the token's dev, or on a prediction market token the market's creator (holding the whole dev share).
        At most ``get_max_creator_fee()``; one change per cooldown (``get_fee_cooldowns``), the first setting is free.
        Refused on a main, paused or not. Adds ``creator_fee``, read from the tx's log (None with a warning when it is
        missing)."""
        me = self._account
        value = to_uint(bps, "bps")
        token = to_address(token, "token")
        info = self.get_token_tax_info(token)
        if info["is_main"]:
            raise ValidationError("not on MAIN", "Fees cannot be set on a main.")
        if info["dev"].lower() != me.lower():
            fns = self._taxes.functions
            is_host = fns.isPrediction(_cs(info["dev"])).call()
            share = fns.devBasisPoints(token, me).call()
            if not (is_host and share == TOTAL_BASIS_POINTS):
                raise ValidationError("only dev", f"Only the token's creator ({info['dev']}) can set its creator fee.")
        if value > info["max_creator_fee"]:
            raise ValidationError("over max", f"Creator fee {value} bps is above the max of {info['max_creator_fee']} bps.")
        self._check_cooldown(token, "creator")
        tx = self._send(self._taxes.functions.setCreatorFee(token, value), "Taxes")
        ev = self._expect_event(tx, self._taxes, "CreatorFeeSet", "Taxes", ("creator_fee",))
        return {**tx, "creator_fee": int(ev["bps"]) if ev else None}

    def set_rewards_fee(self, token: str, bps: Union[int, float]) -> Dict[str, Any]:
        """Set ``token``'s rewards fee (bps on top of the base rate, paid to the token's holders). Caller: the token's dev.
        Needs a rewards-enabled token; at most ``get_token_tax_info(token)["max_rewards_fee"]``; one change per cooldown
        (``get_fee_cooldowns``). Refused on a main, paused or not. Adds ``rewards_fee``, read from the tx's log (None
        with a warning when it is missing)."""
        me = self._account
        value = to_uint(bps, "bps")
        token = to_address(token, "token")
        info = self.get_token_tax_info(token)
        if info["is_main"]:
            raise ValidationError("not on MAIN", "Fees cannot be set on a main.")
        if info["dev"].lower() != me.lower():
            raise ValidationError("only dev", f"Only the token's creator ({info['dev']}) can set its rewards fee.")
        if not info["rewards_enabled"]:
            raise ValidationError("rewards off", "This token was created without holder rewards.")
        if value > info["max_rewards_fee"]:
            raise ValidationError(
                "over max", f"Rewards fee {value} bps is above the max of {info['max_rewards_fee']} bps for this token."
            )
        self._check_cooldown(token, "rewards")
        tx = self._send(self._taxes.functions.setRewardsFee(token, value), "Taxes")
        ev = self._expect_event(tx, self._taxes, "RewardsFeeSet", "Taxes", ("rewards_fee",))
        return {**tx, "rewards_fee": int(ev["bps"]) if ev else None}

    def _check_cooldown(self, token: str, kind: str) -> None:
        c = self.get_fee_cooldowns(token)
        nxt = c["creator_fee_next_change_at"] if kind == "creator" else c["rewards_fee_next_change_at"]
        if not (c["can_change_creator_fee"] if kind == "creator" else c["can_change_rewards_fee"]):
            when = datetime.fromtimestamp(nxt, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
            raise ValidationError("fee cooldown", f"The {kind} fee was changed recently; it can change again at {when}.")


    def get_dev_wallets(self, token: str) -> List[str]:
        """Wallets sharing ``token``'s creator income (their shares: ``get_dev_basis_points``).
        The unallocated rest goes to its dev."""
        return list(self._taxes.functions.getDevWallets(to_address(token, "token")).call())

    def get_dev_basis_points(self, token: str, wallet: str) -> int:
        """``wallet``'s share of ``token``'s creator income, in basis points of 10000."""
        token, wallet = to_address(token, "token"), to_address(wallet, "wallet")
        return int(self._taxes.functions.devBasisPoints(token, wallet).call())

    def get_dev_total_allocated(self, token: str) -> int:
        """Total basis points of ``token``'s creator income given to share wallets (<= 10000)."""
        return int(self._taxes.functions.devTotalAllocated(to_address(token, "token")).call())

    def add_dev_share(self, token: str, wallet: str, basis_points: Union[int, float]) -> Dict[str, Any]:
        """Set ``wallet``'s share of ``token``'s creator income to ``basis_points`` (of 10000; replaces an existing share).
        Caller: the token's dev. Total shares <= 100%, at most 10 share wallets. Not on mains (paused or not).
        Refused, in this order, with 'Cannot set for MAINTOKEN', 'Only DEV can add shares', 'Invalid wallet',
        'Basis points must be >0', 'Exceeds 100%' or 'Max 10 shares'.
        Adds ``basis_points`` (the wallet's share now), read from the tx's log (None with a warning when missing)."""
        me = self._account
        bp = to_uint(basis_points, "basisPoints")
        token, wallet = to_address(token, "token"), to_address(wallet, "wallet")
        self._check_dev_share_caller(token, me, "Only DEV can add shares")
        if is_zero_address(wallet):
            raise ValidationError("Invalid wallet", "A share wallet address is required.")
        if bp <= 0:
            raise ValidationError("Basis points must be >0", "The share must be above 0 basis points.")
        fns = self._taxes.functions
        old_bp = fns.devBasisPoints(token, wallet).call()
        allocated = fns.devTotalAllocated(token).call()
        wallets = self.get_dev_wallets(token)
        new_total = allocated - old_bp + bp
        if new_total > TOTAL_BASIS_POINTS:
            raise ValidationError("Exceeds 100%", f"Shares would total {new_total} of {TOTAL_BASIS_POINTS} basis points.")
        if old_bp == 0 and len(wallets) >= MAX_SHARES:
            raise ValidationError("Max 10 shares", f"A token can have at most {MAX_SHARES} share wallets.")
        tx = self._send(fns.addDevShare(token, wallet, bp), "Taxes")
        ev = self._expect_event(tx, self._taxes, "DevShareSet", "Taxes", ("basis_points",))
        return {**tx, "basis_points": int(ev["basisPoints"]) if ev else None}

    def remove_dev_share(self, token: str, wallet: str) -> Dict[str, Any]:
        """Remove ``wallet``'s share of ``token``'s creator income (it goes back to the dev). Caller: the token's dev.
        Adds ``basis_points`` (0), read from the tx's log (None with a warning when it is missing)."""
        me = self._account
        token, wallet = to_address(token, "token"), to_address(wallet, "wallet")
        self._check_dev_share_caller(token, me, "Only DEV can remove shares")
        fns = self._taxes.functions
        if fns.devBasisPoints(token, wallet).call() == 0:
            raise ValidationError("no share", f"{wallet} has no share of this token's creator income.")
        tx = self._send(fns.removeDevShare(token, wallet), "Taxes")
        ev = self._expect_event(tx, self._taxes, "DevShareSet", "Taxes", ("basis_points",))
        return {**tx, "basis_points": int(ev["basisPoints"]) if ev else None}

    def _check_dev_share_caller(self, token: str, me: str, rule: str) -> None:
        """Refuses a main (paused or not) and any caller that is not the token's dev."""
        if self.client.mains.is_main(token):
            raise ValidationError("Cannot set for MAINTOKEN", "Dev shares cannot be set on a main.")
        dev = self._contract(token, "EcosystemToken").functions.DEV().call()
        if dev.lower() != me.lower():
            raise ValidationError(rule, f"Only the token's creator ({dev}) can change its dev shares.")

    def get_creator_earnings(self, token: str, dev: str) -> Amount:
        """Running total ``dev`` has earned from ``token`` (creator fees, including the creator's part of loan fees),
        never reset, in the token's pair asset."""
        token, dev = to_address(token, "token"), to_address(dev, "dev")
        main = self.client.mains.main_of(token)
        info = self.client.mains.get(main)
        raw = self._taxes.functions.tokenDevEarnings(token, dev).call()
        return to_amount(raw, info.pair_decimals, "pairAsset", info.pair_asset, info.pair_symbol)

    def get_total_creator_income(self, token: str) -> Amount:
        """Everything ``token`` has paid to its creator side, never reset: share wallets plus the dev, including payouts
        that could not reach their recipient (the per-recipient ``get_creator_earnings`` leaves those out). In the
        token's pair asset."""
        token = to_address(token, "token")
        main = self.client.mains.main_of(token)
        info = self.client.mains.get(main)
        raw = self._taxes.functions.totalDevTaxCollected(token).call()
        return to_amount(raw, info.pair_decimals, "pairAsset", info.pair_asset, info.pair_symbol)

    def get_dev_total_earnings(self, dev: str, pair_asset: Optional[str] = None) -> Amount:
        """Running total ``dev`` has earned across all tokens paid in ``pair_asset``
        (default: the default main's pair asset)."""
        dev = to_address(dev, "dev")
        meta = self._pair_asset_meta(pair_asset)
        raw = self._taxes.functions.devTotalEarnings(dev, meta["asset"]).call()
        return to_amount(raw, meta["decimals"], "pairAsset", meta["asset"], meta["symbol"])


    def get_referrals_address(self) -> str:
        """The referral registry fees use, read live (the one ``client.referrals`` talks to)."""
        return self._taxes.functions.referrals().call()

    def get_referrer(self, wallet: str) -> Optional[str]:
        """``wallet``'s referrer as fees see it: None when none is set or when it is a protocol wallet.
        To set one: ``client.referrals.set_referrer``."""
        r = self._taxes.functions.referrerOf(to_address(wallet, "wallet")).call()
        return None if is_zero_address(r) else r

    def payer_of(self, payer: str) -> str:
        """The wallet that fees paid through ``payer`` are credited to: the user behind a protocol contract acting for
        them (loans, the vault, vesting), else ``payer`` itself."""
        return self._taxes.functions.payerOf(to_address(payer, "payer")).call()

    def get_referral_earnings(self, referrer: str, pair_asset: Optional[str] = None) -> Amount:
        """What ``referrer`` has received from referrals (both levels) in ``pair_asset``
        (default: the default main's pair asset)."""
        referrer = to_address(referrer, "referrer")
        meta = self._pair_asset_meta(pair_asset)
        raw = self._taxes.functions.referralEarnings(referrer, meta["asset"]).call()
        return to_amount(raw, meta["decimals"], "pairAsset", meta["asset"], meta["symbol"])


    def _pair_asset_meta(self, pair_asset: Optional[str] = None) -> Dict[str, Any]:
        """Decimals and symbol of a pair asset; the default main's pair asset when omitted."""
        if pair_asset is None:
            m = self.client.mains.default()
            return {"asset": m.pair_asset, "decimals": m.pair_decimals, "symbol": m.pair_symbol}
        pair_asset = to_address(pair_asset, "pairAsset")
        meta = self.client.mains.token_meta(pair_asset)
        return {"asset": pair_asset, "decimals": meta["decimals"], "symbol": meta["symbol"]}
