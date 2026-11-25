"""The mains registry (TypeScript SDK: src/core/mains.ts).

Several mains exist, each with its own pair asset and vault. Tell a main with ``mains.is_main`` (paused mains
included), not with the token's own ``isMain()``, which is false while the main is paused.
Results are cached per client; call ``refresh()`` after state changes.
"""

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from ..config.addresses import is_zero_address
from .base import BaseModule
from .errors import BasisError, NotDeployedError, ValidationError
from .units import Amount, to_address, to_amount

_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


@dataclass(frozen=True)
class MainInfo:
    """One main as the chain reports it."""
    main: str
    key: Optional[str]
    name: str
    symbol: str
    pair_asset: str
    pair_symbol: str
    pair_decimals: int
    pair_unit: int
    vault: str
    active: bool
    trading_enabled: bool
    registered: bool
    is_default: bool


def pair_amount(raw: int, info: MainInfo) -> Amount:
    """An amount in a main's pair asset (scaled by ``pair_unit``)."""
    return to_amount(raw, info.pair_decimals, "pairAsset", info.pair_asset, info.pair_symbol)


def main_amount(raw: int, info: MainInfo) -> Amount:
    """An amount in main tokens (18 decimals)."""
    return to_amount(raw, 18, "main", info.main, info.symbol)


class MainsModule(BaseModule):
    def __init__(self, client):
        super().__init__(client)
        self._cache: Dict[str, MainInfo] = {}
        self._list_cache: Optional[List[MainInfo]] = None
        self._meta_cache: Dict[str, Dict[str, object]] = {}

    @property
    def _swap(self):
        return self._contract(self._require(self.client.addresses["swap"], "Swap"), "Swap")

    def _default_entry(self) -> Optional[dict]:
        book = self.client.addresses
        return next((m for m in book["mains"] if m.get("key") == book["defaultMain"]), None)

    def list(self) -> List[MainInfo]:
        """Every main (paused ones included; mains are never removed from the list)."""
        if is_zero_address(self.client.addresses["swap"]):
            return []
        if self._list_cache is None:
            swap = self._swap
            count = swap.functions.pairCount().call()
            self._list_cache = [self.get(swap.functions.pairList(i).call()) for i in range(count)]
        return self._list_cache

    def default(self) -> MainInfo:
        """The default main (from the address book; it cannot be read on chain)."""
        return self.get(self.default_address())

    def default_address(self) -> str:
        """Address of the default main, without RPC calls. ``NotDeployedError`` while the book's entry is still zero."""
        entry = self._default_entry()
        if not entry:
            raise BasisError(
                f"[basis-sdk] No default main configured for chain {self.client.chain_id} "
                f'(addresses.defaultMain = "{self.client.addresses["defaultMain"]}").'
            )
        if is_zero_address(entry.get("main")):
            raise NotDeployedError(f"Main {entry.get('key')}", self.client.chain_id)
        return Web3.to_checksum_address(entry["main"])

    def get(self, main_or_key: str) -> MainInfo:
        """A main by address or address-book key. Verifies the book entry against the chain."""
        main = self._resolve(main_or_key)
        info = self._cache.get(main.lower())
        if info is None:
            info = self._load(main)
            self._cache[main.lower()] = info
        return info

    def address_of(self, main_or_key: str) -> str:
        """Address of a main given by address or address-book key, without RPC calls (an address goes through
        ``to_address``, rule ``main``: checksummed, not verified on chain; a mixed-case address with a wrong
        checksum is refused)."""
        return self._resolve(main_or_key)

    def is_main(self, token: str) -> bool:
        """True when ``token`` (through ``to_address``) is a main, paused or not."""
        pair_asset, _, _ = self._swap.functions.pairs(to_address(token, "token")).call()
        return not is_zero_address(pair_asset)

    def main_of(self, token: str) -> str:
        """The main a token trades against: the token itself for a main, else the main it was created on
        (checksummed). An address that is not a BASIS token (an outside token, a wallet) is refused with
        ``ValidationError("Not an Ecosystem token")``; RPC failures are raised as they are. ``token`` through
        ``to_address``."""
        token = to_address(token, "token")
        if self.is_main(token):
            return token
        try:
            return Web3.to_checksum_address(self._contract(token, "EcosystemToken").functions.quoteToken().call())
        except (ContractLogicError, BadFunctionCallOutput) as e:
            raise ValidationError("Not an Ecosystem token", f"{token} is not a BASIS token (no quoteToken()).") from e

    def info_of_token(self, token: str) -> MainInfo:
        """MainInfo of the main a token trades against (the token's own when it is a main); 'Not an Ecosystem token' as
        ``main_of``."""
        return self.get(self.main_of(token))

    def token_meta(self, token: str) -> Dict[str, object]:
        """ERC-20 decimals and symbol of any token (cached; both are immutable). ``token`` through ``to_address``."""
        token = to_address(token, "token")
        key = token.lower()
        meta = self._meta_cache.get(key)
        if meta is None:
            fns = self._contract(token, "ERC20").functions
            meta = {"decimals": fns.decimals().call(), "symbol": fns.symbol().call()}
            self._meta_cache[key] = meta
        return meta

    def pair_asset_of(self, main: str) -> str:
        return self.get(main).pair_asset

    def vault_of(self, main: str) -> str:
        return self.get(main).vault

    def refresh(self) -> None:
        """Clear cached main data (e.g. after a pair is paused or trading enabled)."""
        self._cache.clear()
        self._list_cache = None

    def _resolve(self, main_or_key: str) -> str:
        if isinstance(main_or_key, str) and _ADDRESS.fullmatch(main_or_key):
            return to_address(main_or_key, "main")
        entry = next((m for m in self.client.addresses["mains"] if m.get("key") == main_or_key), None)
        if not entry:
            known = ", ".join(m.get("key", "?") for m in self.client.addresses["mains"]) or "(none)"
            raise BasisError(f'[basis-sdk] Unknown main "{main_or_key}". Known keys: {known}')
        if is_zero_address(entry.get("main")):
            raise NotDeployedError(f"Main {entry.get('key')}", self.client.chain_id)
        return Web3.to_checksum_address(entry["main"])

    def _load(self, main: str) -> MainInfo:
        swap = self._swap
        pair_asset, vault, active = swap.functions.pairs(main).call()
        if is_zero_address(pair_asset):
            raise BasisError(f"[basis-sdk] {main} is not a main on SWAP {swap.address}.")

        entry = next((m for m in self.client.addresses["mains"] if m["main"].lower() == main.lower()), None)
        if entry and (entry["pairAsset"].lower() != pair_asset.lower() or entry["vault"].lower() != vault.lower()):
            raise BasisError(
                f'[basis-sdk] Address book entry "{entry["key"]}" disagrees with SWAP.pairs({main}): '
                f'book pairAsset {entry["pairAsset"]} / vault {entry["vault"]}, chain {pair_asset} / {vault}.'
            )

        m = self._contract(main, "MainToken").functions
        erc20 = self._contract(pair_asset, "ERC20").functions
        pair_unit = m.PAIR_UNIT().call()
        pair_decimals = erc20.decimals().call()
        if pair_unit != 10 ** pair_decimals:
            raise BasisError(f"[basis-sdk] {main}: PAIR_UNIT {pair_unit} does not match the pair asset's {pair_decimals} decimals.")

        factory = self.client.addresses["factory"]
        registered = False
        if not is_zero_address(factory):
            pair = self._contract(factory, "Factory").functions.getPair(main, pair_asset).call()
            registered = pair.lower() == main.lower()

        default = self._default_entry()
        return MainInfo(
            main=main,
            key=entry["key"] if entry else None,
            name=m.name().call(),
            symbol=m.symbol().call(),
            pair_asset=pair_asset,
            pair_symbol=erc20.symbol().call(),
            pair_decimals=pair_decimals,
            pair_unit=pair_unit,
            vault=vault,
            active=active,
            trading_enabled=m.tradingEnabled().call(),
            registered=registered,
            is_default=bool(default) and default["main"].lower() == main.lower(),
        )
