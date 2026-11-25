"""The paper site's play tokens (tUSDG, tNVDA, tETH; the pair assets of STASIS, tNVDA+ and tETH+; TypeScript SDK:
src/modules/Paper.ts).

Every wallet claims a fixed amount of each, once (``get_claim_amount``); the tokens have no value. Works on the
``robinhoodPaper`` preset (and a custom paper config): on the real site every call raises
``ValidationError("paper only")`` before any RPC call.

``token`` everywhere: a paper token's address, or a main key (its pair asset); default: the default main's pair asset.
"""

import re
from typing import Any, Dict, Optional

from web3 import Web3

from ..config.addresses import is_zero_address
from ..core.base import BaseModule
from ..core.errors import BasisError, NotDeployedError, ValidationError
from ..core.units import Amount, to_address, to_amount

_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")


class PaperModule(BaseModule):
    """Claim and read the paper site's play tokens."""

    @property
    def available(self) -> bool:
        """True on a paper deployment (``chain_config.paper``)."""
        return self.client.chain_config.paper is True

    def claim(self, token: Optional[str] = None) -> Dict[str, Any]:
        """Mints one claim of the token (``get_claim_amount``) to the signer, once per wallet: refused with 'already
        claimed' after that. Adds ``amount``, read from the tx's log (None with a warning when it is missing)."""
        t = self._token_of(token)
        me = self._account
        meta = self.client.mains.token_meta(t)
        contract = self._contract(t, "PaperAsset")
        if contract.functions.hasClaimed(me).call():
            raise ValidationError("already claimed", f"This wallet already claimed {meta['symbol']}: one claim per wallet.")
        tx = self._send(contract.functions.claim(), "PaperAsset")
        ev = self._expect_event(tx, contract, "Claimed", "PaperAsset", ("amount",))
        return {**tx, "amount": to_amount(ev["amount"], meta["decimals"], "token", t, meta["symbol"]) if ev else None}

    def has_claimed(self, token: Optional[str] = None, wallet: Optional[str] = None) -> bool:
        """True once ``wallet`` (default: the signer) has claimed ``token``."""
        t = self._token_of(token)
        who = self._account if wallet is None else to_address(wallet, "wallet")
        return bool(self._contract(t, "PaperAsset").functions.hasClaimed(who).call())

    def get_claim_amount(self, token: Optional[str] = None) -> Amount:
        """What one claim of ``token`` mints."""
        t = self._token_of(token)
        raw = self._contract(t, "PaperAsset").functions.claimAmount().call()
        meta = self.client.mains.token_meta(t)
        return to_amount(raw, meta["decimals"], "token", t, meta["symbol"])

    def _token_of(self, token: Optional[str] = None) -> str:
        """The token of a call, from the address book, without RPC calls. The paper guard of every method."""
        if not self.available:
            raise ValidationError("paper only",
                                  f"Paper tokens exist on the paper deployment only, not on {self.client.chain_config.key}.")
        if isinstance(token, str) and _ADDRESS_RE.fullmatch(token):
            return to_address(token, "token")
        book = self.client.addresses
        key = token if token is not None else book["defaultMain"]
        entry = next((m for m in book["mains"] if m.get("key") == key), None)
        if entry is None:
            known = ", ".join(m.get("key") for m in book["mains"]) or "(none)"
            raise BasisError(f'[basis-sdk] Unknown main "{key}". Known keys: {known}')
        if is_zero_address(entry.get("pairAsset")):
            raise NotDeployedError("PaperAsset", self.client.chain_id)
        return Web3.to_checksum_address(entry["pairAsset"])
