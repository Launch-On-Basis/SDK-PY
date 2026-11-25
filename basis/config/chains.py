"""Network presets (TypeScript SDK: src/config/chains.ts)."""

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Union

from .addresses import ROBINHOOD_ADDRESSES, ROBINHOOD_PAPER_ADDRESSES, empty_address_book, merge_address_book

MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11"

ROBINHOOD_RPC = "https://rpc.mainnet.chain.robinhood.com"


@dataclass
class ChainConfig:
    """Everything the SDK needs to know about one network."""
    key: str
    chain_id: int
    name: str
    rpc_url: str
    api_domain: str
    addresses: Dict[str, Any] = field(default_factory=empty_address_book)
    multicall3: Optional[str] = None
    paper: bool = False
    contract_block_is_l1: bool = False
    block_time_ms: Optional[float] = None


CHAINS: Dict[str, ChainConfig] = {
    "robinhood": ChainConfig("robinhood", 4663, "Robinhood Chain", ROBINHOOD_RPC, "https://launchonbasis.com",
                             ROBINHOOD_ADDRESSES, multicall3=MULTICALL3, contract_block_is_l1=True),
    "robinhoodPaper": ChainConfig("robinhoodPaper", 4663, "Robinhood Chain", ROBINHOOD_RPC,
                                  "https://paper.launchonbasis.com", ROBINHOOD_PAPER_ADDRESSES,
                                  multicall3=MULTICALL3, paper=True, contract_block_is_l1=True),
}

DEFAULT_CHAIN = "robinhoodPaper"

LOCAL_CHAIN_IDS = (31337, 1337)
BLOCK_TIME_MS: Dict[int, float] = {4663: 100}
DEFAULT_BLOCK_TIME_MS = 1000


def _is_ms(v: Any) -> bool:
    """A block time a config may give: a number from 0 (0: a local node, no block time; not a bool, not inf / nan)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0


def block_time_ms(chain_id: Any, own: Any = None) -> float:
    """The block time the SDK goes by on chain ``chain_id``, ms: 0 on a local node (:data:`LOCAL_CHAIN_IDS`) or without
    a chain id; else ``own`` (a config's value, when a number from 0: 0 declares a local node on another chain id, no
    block time), else :data:`BLOCK_TIME_MS`, else :data:`DEFAULT_BLOCK_TIME_MS`. The one rule of the pinned reads after
    a write (``pinned_block``) and of ``next_nonce``, so both SDKs decide the same for the same config. TS:
    blockTimeMs."""
    if isinstance(chain_id, bool) or not isinstance(chain_id, int) or chain_id <= 0 or chain_id in LOCAL_CHAIN_IDS:
        return 0
    if _is_ms(own):
        return own
    return BLOCK_TIME_MS.get(chain_id, DEFAULT_BLOCK_TIME_MS)


def chain_block_time_ms(cfg: Any) -> float:
    """:func:`block_time_ms` of a chain config: its ``chain_id`` and ``block_time_ms`` (TS: the viem chain's ``id`` and
    ``blockTime``); anything without them counts as no chain id (0). TS: chainBlockTimeMs."""
    return block_time_ms(getattr(cfg, "chain_id", None), getattr(cfg, "block_time_ms", None))


def _shown(v: Any) -> str:
    """What a value is, for a refusal message (TS: ``shown``)."""
    if v is None:
        return "none"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return "a list"
    if isinstance(v, str):
        return f"'{v}'"
    return "an object" if isinstance(v, dict) else str(v)


def resolve_chain(chain: Union[str, ChainConfig, None]) -> ChainConfig:
    """Resolve a chain option: None is the default preset (``robinhood``), a str one of the preset keys (``""`` is
    refused), a :class:`ChainConfig` a custom config for a chain with no preset: its ``chain_id`` a whole number above
    0, its ``block_time_ms`` None or ms from 0 (:func:`chain_block_time_ms`; 0: a local node, no block time), and its
    ``addresses`` an address book
    dict that passes :func:`chain_address_book` (known keys, valid addresses).
    Anything else raises ``ValidationError("chain")``. The config itself is returned as it is. Same rule as TS
    ``resolveChain`` (undefined there is the default; a custom config's ``chain.id`` and ``addresses`` are checked the
    same way)."""
    from ..core.errors import ValidationError

    if chain is None:
        return CHAINS[DEFAULT_CHAIN]
    if isinstance(chain, ChainConfig):
        cid = chain.chain_id
        if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
            raise ValidationError("chain", "a custom chain config needs chain_id: the chain id (a whole number), got "
                                           f"{_shown(cid)}.")
        bt = getattr(chain, "block_time_ms", None)
        if bt is not None and not _is_ms(bt):
            raise ValidationError("chain", "a custom chain config's block_time_ms must be the block time in ms (a number "
                                           f"above 0; 0 for a local node), or None, got {_shown(bt)}.")
        if not isinstance(chain.addresses, dict):
            raise ValidationError("chain", "a custom chain config needs addresses: an address book dict (e.g. "
                                           "{**empty_address_book(), \"swap\": \"0x...\"}), got "
                                           f"{_shown(chain.addresses)}.")
        chain_address_book(chain)
        return chain
    known = ", ".join(CHAINS)
    if isinstance(chain, str):
        if chain not in CHAINS:
            raise ValidationError("chain", f'Unknown chain "{chain}". Known: {known}')
        return CHAINS[chain]
    got = "a list" if isinstance(chain, (list, tuple)) else type(chain).__name__
    raise ValidationError("chain", f"chain must be a preset name ({known}) or a chain config, got {got}.")


def chain_address_book(cfg: ChainConfig) -> Dict[str, Any]:
    """The address book a chain config gives the client: a preset's as it is; a custom config's checked and built over
    :func:`~basis.config.addresses.empty_address_book` with :func:`~basis.config.addresses.merge_address_book` (the
    ``addresses`` option's rule: known keys only, addresses ``0x`` + 40 hex with a correct checksum when in mixed case,
    ``mains`` entries complete), so a key it leaves out (or sets to None) is zero, not deployed, and a module using it
    raises NotDeployedError. A bad book raises ``ValidationError("chain")``. The client's ``addresses`` option then goes
    over it. Internal. TS: ``chainAddressBook``."""
    from ..core.errors import ValidationError

    if any(cfg is c for c in CHAINS.values()):
        return cfg.addresses
    try:
        return merge_address_book(empty_address_book(), cfg.addresses)
    except ValidationError as e:
        why = str(e).removeprefix("[basis-sdk] ")
        raise ValidationError("chain", f"a custom chain config has a bad address book: {why}") from e
