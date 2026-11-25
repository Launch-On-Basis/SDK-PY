"""The dapp's ``/contracts.json``: the address book it describes (when it publishes addresses), and whether the
deployment it names (``network``, or an older file's ``paper`` flag) fits the client's chain. TypeScript SDK:
src/core/contractsJson.ts."""

from typing import Any, Dict, Optional

from ..config.addresses import ADDRESS_KEYS, is_zero_address


def _eq(a: Any, b: Any) -> bool:
    return isinstance(a, str) and isinstance(b, str) and a.lower() == b.lower()


def _b(v: bool) -> str:
    return "true" if v else "false"


KEEP_IF_ZERO_KEYS = ("erc8004Registry", "wrappedNative")


def contracts_flag_mismatch(remote: Dict[str, Any], chain: Any) -> Optional[str]:
    """The problem with the deployment a ``/contracts.json`` names, for ``chain``, or None. Real and paper share chain
    4663, so the file's ``network`` (``robinhood`` on the real site, ``robinhood-paper`` on the paper site: a
    ``-paper`` key is a paper deployment) or an older file's ``paper`` flag is what tells them apart; each is compared
    with the preset's ``paper``. A file with neither (or other types) is no problem. ``prelaunch`` needs nothing: its
    BASIS contracts are zero, which the address comparison reads as not deployed."""
    mine = chain.paper is True
    what = "the paper deployment" if mine else "not the paper deployment"
    flag = remote.get("paper")
    if isinstance(flag, bool) and flag != mine:
        return f"paper: SDK chain {chain.key} is {what}, contracts.json says paper {_b(flag)}"
    network = remote.get("network")
    if isinstance(network, str) and network and network.lower().endswith("-paper") != mine:
        return f"network: SDK chain {chain.key} is {what}, contracts.json serves network {network}"
    return None


def address_book_from_contracts_json(remote: Dict[str, Any], base: Dict[str, Any]) -> Dict[str, Any]:
    """Address book from a dapp ``/contracts.json`` (camelCase keys incl. ``mainLens``, ``referrals``, ``mains[]``), over
    ``base``. Accepts the aliases ``marketTrading`` / ``privateMarket`` / ``reader`` for ``tranche`` / ``tranchePrivate``
    / ``trancheReader``; a main listed without a ``vault`` takes its own ``staking`` value, else the file's
    ``staking``. A zero ``erc8004Registry`` / ``wrappedNative`` keeps the base value (``KEEP_IF_ZERO_KEYS``). The
    ``paper`` / ``prelaunch`` flags are not address-book keys: check them with :func:`contracts_flag_mismatch`
    (``client.check_contracts`` does)."""
    out = {**base, "mains": list(base["mains"])}
    alias = {"tranche": "marketTrading", "tranchePrivate": "privateMarket", "trancheReader": "reader"}
    for key in ADDRESS_KEYS:
        v = remote.get(key)
        if v is None and key in alias:
            v = remote.get(alias[key])
        if not isinstance(v, str):
            continue
        if key in KEEP_IF_ZERO_KEYS and is_zero_address(v) and not is_zero_address(base.get(key)):
            continue
        out[key] = v
    if isinstance(remote.get("mains"), list):
        mains = []
        for m in remote["mains"]:
            e = {"key": m.get("key"), "label": m.get("label") or m.get("key"), "main": m.get("main"),
                 "pairAsset": m.get("pairAsset"), "vault": m.get("vault") or m.get("staking") or remote.get("staking")}
            if m.get("pairSymbol") is not None:
                e["pairSymbol"] = m["pairSymbol"]
            if m.get("pairDecimals") is not None:
                e["pairDecimals"] = int(m["pairDecimals"])
            mains.append(e)
        out["mains"] = mains
        default = (next((m for m in mains if _eq(m["main"], remote.get("mainToken"))), None)
                   or next((m for m in mains if m["key"] == base["defaultMain"]), None)
                   or (mains[0] if mains else None))
        if default:
            out["defaultMain"] = default["key"]
    return out
