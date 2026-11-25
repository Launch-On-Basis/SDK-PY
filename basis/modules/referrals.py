"""The referral registry (TypeScript SDK: src/modules/Referrals.ts).

A wallet names its referrer once, here. What it earns and pays is ``client.taxes``: ``taxes.get_referrer`` (what fees
count; a protocol wallet counts as none) and ``taxes.get_referral_earnings``. A registry can have a ``previous``
one, and a referrer set there leads.
"""

from typing import Any, Dict, Optional

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import NotDeployedError, ValidationError
from ..core.pinned import pinned_call
from ..core.redact import redact_urls
from ..core.units import to_address


def _cs(address: str) -> str:
    return Web3.to_checksum_address(address)


class ReferralsModule(BaseModule):
    """Who referred whom. Every read goes to the registry; fees and earnings are ``client.taxes``."""

    @property
    def deployed(self) -> bool:
        """Whether the registry can be found on this chain (the ``taxes`` or the ``referrals`` address is set)."""
        return not is_zero_address(self.client.addresses["taxes"]) or not is_zero_address(self.client.addresses["referrals"])

    def get_address(self) -> str:
        """The registry: the one fees use, read live (``taxes.get_referrals_address()``; fees only count referrers set
        there), else, while ``taxes`` has no address on this chain, the address book's ``referrals``. NotDeployedError
        when neither is there."""
        if not is_zero_address(self.client.addresses["taxes"]):
            return _cs(self.client.taxes.get_referrals_address())
        book = self.client.addresses["referrals"]
        if is_zero_address(book):
            raise NotDeployedError("Referrals", self.client.chain_id)
        return _cs(book)

    @property
    def _registry(self):
        return self._contract(self.get_address(), "Referrals")

    def get_referrer(self, wallet: str) -> Optional[str]:
        """``wallet``'s entry in the registry (a previous registry's entry leads), or None. Fees use
        ``taxes.get_referrer``."""
        wallet = to_address(wallet, "wallet")
        return self._referrer_at(self.get_address(), wallet)

    def get_referral_count(self, referrer: str) -> int:
        """Wallets that named ``referrer`` in this registry (direct, level 1)."""
        referrer = to_address(referrer, "referrer")
        return int(self._registry.functions.referralCount(referrer).call())

    def is_closed(self) -> bool:
        """True while nobody can set a referrer here (what is set stays)."""
        return bool(self._registry.functions.closed().call())

    def get_previous(self) -> Optional[str]:
        """The registry before this one, or None for the first."""
        p = self._registry.functions.previous().call()
        return None if is_zero_address(p) else p

    def set_referrer(self, referrer: str) -> Dict[str, Any]:
        """Name the signer's referrer. **Permanent: it can be set once and the wallet cannot change it.** The signer then
        pays a lower base fee, and the referrer earns part of the signer's base fees (and a smaller part one level
        further down). A wallet with a referrer in the previous registry locks that one in here instead of ``referrer``
        (result ``referrer`` and a warning say so). Refused, in this order, with 'closed'; 'referrer set' (the wallet
        already has one); 'invalid referrer' (zero, or the signer itself); 'referral loop' (the referrer was referred by
        the signer). Adds ``referrer``, read from the tx's log (None with a warning when it is missing).

        After the tx, the SDK reads whether fees count the new referrer (``taxes.get_referrer``, at the tx's block): a
        protocol wallet can be named in the registry, but fees count it as no referrer (no discount, no earnings), and a
        warning then says so. The tx is confirmed either way: never resend it (a failed read is a warning too)."""
        me = self._account
        referrer = to_address(referrer, "referrer")
        address = self.get_address()
        fns = self._contract(address, "Referrals").functions
        closed = fns.closed().call()
        previous = fns.previous().call()
        current = self._referrer_at(address, me)
        if closed:
            raise ValidationError("closed", "The referral registry is closed: no new referrer can be set.")
        old = None if is_zero_address(previous) else self._referrer_at(previous, me)
        if old is None and current is not None:
            raise ValidationError("referrer set", f"This wallet already has a referrer ({current}); it cannot change it.")
        if old is None:
            if is_zero_address(referrer):
                raise ValidationError("invalid referrer", "A referrer address is required.")
            if referrer.lower() == me.lower():
                raise ValidationError("invalid referrer", "A wallet cannot refer itself.")
        target = old if old is not None else referrer
        above = self._referrer_at(address, target)
        if above is not None and above.lower() == me.lower():
            raise ValidationError("referral loop", f"{target} was referred by this wallet.")
        contract = self._contract(address, "Referrals")
        tx = self._send(contract.functions.setReferrer(referrer), "Referrals")
        ev = self._expect_event(tx, contract, "ReferrerSet", "Referrals", ("referrer",))
        recorded = _cs(ev["referrer"]) if ev else None
        if recorded is not None and recorded.lower() != referrer.lower():
            tx["warnings"].append(f"The previous registry's referrer {recorded} was locked in instead of {referrer}.")
        self._warn_if_fees_ignore(tx, me, recorded if recorded is not None else referrer)
        return {**tx, "referrer": recorded}

    def _warn_if_fees_ignore(self, tx: Dict[str, Any], me: str, named: str) -> None:
        """After a confirmed ``set_referrer``: when fees count the signer's new referrer as none (a protocol wallet), a
        warning says the discount and the earnings are lost. Read at the tx's block (a node a few blocks behind is
        retried), never raises: a failed read is a warning too. Skipped while ``taxes`` has no address on this chain."""
        taxes = self.client.addresses["taxes"]
        if is_zero_address(taxes):
            return
        try:
            block = tx["receipt"]["blockNumber"]
            counted = pinned_call(self._contract(_cs(taxes), "Taxes").functions.referrerOf(_cs(me)), block)
            if is_zero_address(counted):
                tx["warnings"].append(
                    f"Fees count {named} as no referrer (it is a protocol wallet): this wallet gets no fee discount and "
                    f"{named} earns nothing from its fees. The referrer is set for good and cannot be changed.")
        except Exception as e:  # noqa: BLE001
            tx["warnings"].append(
                f"The referrer is set, but whether fees count it could not be read ({redact_urls(e)}): check with "
                "taxes.get_referrer.")

    def _referrer_at(self, address: str, wallet: str) -> Optional[str]:
        r = self._contract(address, "Referrals").functions.referrerOf(_cs(wallet)).call()
        return None if is_zero_address(r) else r
