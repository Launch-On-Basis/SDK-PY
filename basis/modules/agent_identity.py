import json
import base64
import logging
from typing import Optional, Dict, Any
from web3 import Web3
from .factory import load_abi

logger = logging.getLogger(__name__)

# ERC-8004 Identity Registry on BSC
IDENTITY_REGISTRY = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"

# Inline ABI for the Identity Registry
IDENTITY_ABI = [
    {"inputs":[{"name":"agentURI","type":"string"}],"name":"register","outputs":[{"name":"agentId","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"register","outputs":[{"name":"agentId","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"tokenId","type":"uint256"}],"name":"ownerOf","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"}],"name":"getAgentWallet","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"metadataKey","type":"string"}],"name":"getMetadata","outputs":[{"name":"","type":"bytes"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"metadataKey","type":"string"},{"name":"metadataValue","type":"bytes"}],"name":"setMetadata","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"},{"name":"newURI","type":"string"}],"name":"setAgentURI","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"agentId","type":"uint256"}],"name":"isAuthorizedOrOwner","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"agentId","type":"uint256"}],"name":"tokenURI","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"anonymous":False,"inputs":[{"indexed":True,"name":"agentId","type":"uint256"},{"indexed":False,"name":"agentURI","type":"string"},{"indexed":True,"name":"owner","type":"address"}],"name":"Registered","type":"event"},
]


class AgentSyncError(Exception):
    """Raised when on-chain registration succeeded but backend sync failed."""
    def __init__(self, message: str, tx_hash: str, agent_id: int):
        super().__init__(message)
        self.tx_hash = tx_hash
        self.agent_id = agent_id


class AgentIdentityModule:
    def __init__(self, client):
        self.client = client
        self.registry_address = Web3.to_checksum_address(IDENTITY_REGISTRY)
        self._contract = self.client.web3.eth.contract(
            address=self.registry_address, abi=IDENTITY_ABI
        )

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def _build_metadata_uri(self, wallet: str, config: Optional[Dict[str, Any]] = None) -> str:
        """Build base64-encoded metadata URI for on-chain registration."""
        config = config or {}
        metadata = {
            "type": "https://eips.ethereum.org/EIPS/eip-8004#registration-v1",
            "name": config.get("name", "Basis Agent"),
            "description": config.get("description"),
            "image": config.get("image"),
            "website": "https://launchonbasis.com",
            "profile": f"https://launchonbasis.com/reef/profile/{wallet}",
            "protocol": "basis",
            "active": True,
            "capabilities": config.get("capabilities", ["trading"]),
            "supportedTrust": ["reputation"],
        }
        json_str = json.dumps(metadata)
        b64 = base64.b64encode(json_str.encode()).decode()
        return f"data:application/json;base64,{b64}"

    def is_registered(self, wallet: str) -> bool:
        """Check if a wallet has an agent NFT on the Identity Registry."""
        checksum = Web3.to_checksum_address(wallet)
        balance = self._contract.functions.balanceOf(checksum).call()
        return balance > 0

    def get_agent_id_from_chain(self, wallet: str) -> Optional[int]:
        """Look up the agentId for a wallet by scanning Registered events on-chain.
        Returns the agentId or None if not found.
        """
        checksum = Web3.to_checksum_address(wallet)
        registered_event = self._contract.events.Registered()
        logs = registered_event.get_logs(
            fromBlock=0,
            argument_filters={"owner": checksum}
        )
        if not logs:
            return None
        # Return the most recent registration
        return logs[-1]["args"]["agentId"]

    def register(self, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Register the current wallet as an ERC-8004 agent. Returns hash and agentId."""
        if not self.client.account:
            raise ValueError("Private key required to register as agent.")

        uri = self._build_metadata_uri(self.client.account.address, config)
        func = self._contract.functions.register(uri)
        result = self.client.send_transaction(func)

        # Parse agentId from Registered event
        agent_id = 0
        receipt = result["receipt"]
        for log_entry in receipt.get("logs", []):
            addr = log_entry.get("address", "")
            if addr.lower() == self.registry_address.lower():
                topics = log_entry.get("topics", [])
                if len(topics) >= 2:
                    # topics[0] = event sig, topics[1] = indexed agentId
                    raw = topics[1]
                    if isinstance(raw, bytes):
                        agent_id = int.from_bytes(raw, "big")
                    else:
                        agent_id = int(raw, 16) if isinstance(raw, str) else int(raw)
                    break

        self._sync_tx(result['hash'])
        result["agentId"] = agent_id

        # Force sync to backend API — retry up to 3 times
        import time
        sync_err = None
        for attempt in range(3):
            try:
                self._sync_to_api(self.client.account.address, agent_id, config)
                sync_err = None
                break
            except Exception as e:
                sync_err = e
                if attempt < 2:
                    time.sleep(attempt + 1)
        if sync_err:
            raise AgentSyncError(
                f"On-chain registration succeeded (agentId: {agent_id}, tx: {result['hash']}) "
                f"but backend sync failed after 3 attempts: {sync_err}. Call register_and_sync() to retry.",
                tx_hash=result['hash'],
                agent_id=agent_id,
            )

        return result

    def register_and_sync(self, config: Optional[Dict[str, Any]] = None) -> int:
        """Full registration flow: check on-chain, register if needed, sync to API.
        Returns the agentId.
        """
        if not self.client.account:
            raise ValueError("Private key required.")

        address = self.client.account.address

        # Check if already registered
        if self.is_registered(address):
            # Check if API already has it
            api_agent = self.lookup_from_api(address)
            if api_agent and api_agent.get("isAgent"):
                return api_agent["agent"]["agentId"]
            # On-chain but not in API — get real agentId from chain events, then force sync
            chain_agent_id = self.get_agent_id_from_chain(address)
            if chain_agent_id is None:
                raise RuntimeError("Agent shows as registered (balanceOf > 0) but no Registered event found on-chain")
            self._sync_to_api(address, chain_agent_id, config)
            synced = self.lookup_from_api(address)
            if synced and synced.get("isAgent"):
                return synced["agent"]["agentId"]
            raise RuntimeError("Agent registered on-chain but backend sync failed — API still shows isAgent: false")

        # Register on-chain (register() already forces sync)
        result = self.register(config)
        agent_id = result["agentId"]

        return agent_id

    def _sync_to_api(self, wallet: str, agent_id: int, config: Optional[Dict[str, Any]] = None):
        """Save registration to backend API."""
        import requests
        config = config or {}
        body = {
            "wallet": wallet,
            "agentId": agent_id,
            "name": config.get("name", "Basis Agent"),
            "description": config.get("description"),
        }
        # Use the API session (has SIWE cookie)
        self.client.api._session_request("POST", "/agents", json=body)

    def lookup_from_api(self, wallet: str) -> Optional[Dict[str, Any]]:
        """Look up an agent by wallet via the API."""
        try:
            import requests
            res = requests.get(f"{self.client.api_domain}/api/agents/{wallet}")
            if res.ok:
                return res.json()
        except Exception:
            pass
        return None

    def list_agents(self, page: int = 1, limit: int = 20) -> Dict[str, Any]:
        """List all registered agents via the API."""
        import requests
        res = requests.get(f"{self.client.api_domain}/api/agents?page={page}&limit={limit}")
        res.raise_for_status()
        return res.json()

    def get_agent_uri(self, agent_id: int) -> str:
        """Get the tokenURI for a registered agent (on-chain)."""
        return self._contract.functions.tokenURI(agent_id).call()

    def get_agent_wallet(self, agent_id: int) -> str:
        """Get the wallet linked to an agent ID (on-chain)."""
        return self._contract.functions.getAgentWallet(agent_id).call()

    def get_metadata(self, agent_id: int, key: str) -> bytes:
        """Get metadata for an agent by key (on-chain)."""
        return self._contract.functions.getMetadata(agent_id, key).call()

    def set_agent_uri(self, agent_id: int, new_uri: str) -> Dict[str, Any]:
        """Update the agent's URI (on-chain). Must be the owner."""
        func = self._contract.functions.setAgentURI(agent_id, new_uri)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result
