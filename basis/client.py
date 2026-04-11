import logging
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from web3 import Web3
from eth_account import Account
from eth_account.messages import encode_defunct

from .api import BasisAPI
from .modules.factory import FactoryModule
from .modules.trading import TradingModule
from .modules.prediction_markets import PredictionMarketsModule
from .modules.order_book import OrderBookModule
from .modules.loans import LoansModule
from .modules.vesting import VestingModule
from .modules.staking import StakingModule
from .modules.market_resolver import MarketResolverModule
from .modules.private_markets import PrivateMarketsModule
from .modules.agent_identity import AgentIdentityModule
from .modules.market_reader import MarketReaderModule
from .modules.leverage_simulator import LeverageSimulatorModule
from .modules.taxes import TaxesModule

logger = logging.getLogger(__name__)

DEFAULT_RPC_URL = "https://bsc-dataseed.binance.org/"
BSC_CHAIN_ID = 56

# Hardcoded defaults — used as fallback when the remote contracts.json is unreachable
DEFAULT_ADDRESSES = {
    "factory": "0xB6BA282f29A7C67059f4E9D0898eE58f5C79960D",
    "swap": "0x9F9cF98F68bDbCbC5cf4c6402D53cEE1D180715f",
    "marketTrading": "0x396216fc9d2c220afD227B59097cf97B7dEaCb57",
    "loanHub": "0xFe19644d52fD0014EBa40c6A8F4Bfee4Ce3B2449",
    "vesting": "0xedd987c7723B9634b0Aa6161258FED3e89F9094C",
    "usdb": "0x42bcF288e51345c6070F37f30332ee5090fC36BF",
    "mainToken": "0x3067ce754a36d0a2A1b215C4C00315d9Da49EF15",
    "staking": "0x1FE7189270fb93c32a1fEfA71d1795c05C41cb33",
    "resolver": "0xB5FFCCB422531Cf462ec430170f85d8dD3dC3f57",
    "privateMarket": "0x28675A82ee3c2e6d2C85887Ea587FbDD3E3C86EE",
    "reader": "0xF406cA6403c57Ad04c8E13F4ae87b3732daa087d",
    "leverage": "0xeffb140d821c5B20EFc66346Cf414EeAC8A8FDB2",
    "taxes": "0x4501d1279273c44dA483842ED17b5451e7d3A601",
}


class BasisClient:
    """Client for the Basis protocol on BSC.

    The constructor is synchronous and lightweight -- it does **not** perform
    network calls.  Use the :meth:`create` classmethod when you want
    automatic RPC validation, SIWE authentication, and API-key provisioning.
    """

    MEGAFUEL_RPC = "https://bsc-megafuel.nodereal.io/"

    def __init__(
        self,
        rpc_url: str = DEFAULT_RPC_URL,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        api_domain: str = "https://launchonbasis.com",
        gasless: bool = True,  # If True, transactions try BSC Megafuel (zero gas) first, falling back to regular RPC
        factory_address: str = DEFAULT_ADDRESSES["factory"],
        swap_address: str = DEFAULT_ADDRESSES["swap"],
        market_trading_address: str = DEFAULT_ADDRESSES["marketTrading"],
        loan_hub_address: str = DEFAULT_ADDRESSES["loanHub"],
        vesting_address: str = DEFAULT_ADDRESSES["vesting"],
        usdb_address: str = DEFAULT_ADDRESSES["usdb"],
        main_token_address: str = DEFAULT_ADDRESSES["mainToken"],
        staking_address: str = DEFAULT_ADDRESSES["staking"],
        resolver_address: str = DEFAULT_ADDRESSES["resolver"],
        private_market_address: str = DEFAULT_ADDRESSES["privateMarket"],
        reader_address: str = DEFAULT_ADDRESSES["reader"],
        leverage_address: str = DEFAULT_ADDRESSES["leverage"],
        taxes_address: str = DEFAULT_ADDRESSES["taxes"],
    ):
        self.rpc_url = rpc_url
        self.api_domain = api_domain
        self.api_key: Optional[str] = api_key
        self.gasless = gasless
        self.web3 = Web3(Web3.HTTPProvider(rpc_url))
        # BSC is a PoA chain — inject the middleware to handle extraData
        try:
            from web3.middleware import ExtraDataToPOAMiddleware
            self.web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        except ImportError:
            # Older web3.py versions use geth_poa_middleware
            from web3.middleware import geth_poa_middleware
            self.web3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.account = None
        self.usdb_address = Web3.to_checksum_address(usdb_address)
        self.main_token_address = Web3.to_checksum_address(main_token_address)

        if private_key:
            self.account = Account.from_key(private_key)
            logger.info("BasisClient initialized with private key.")
        else:
            logger.info("BasisClient initialized in stateless mode.")

        # Initialize API wrapper
        self.api = BasisAPI(self)

        # Initialize on-chain modules
        self.factory = FactoryModule(self, factory_address)
        self.trading = TradingModule(self, swap_address)
        self.prediction_markets = PredictionMarketsModule(self, market_trading_address)
        self.order_book = OrderBookModule(self, market_trading_address)
        self.loans = LoansModule(self, loan_hub_address)
        self.vesting = VestingModule(self, vesting_address)
        self.staking = StakingModule(self, staking_address)
        self.resolver = MarketResolverModule(self, resolver_address)
        self.private_markets = PrivateMarketsModule(self, private_market_address)
        self.market_reader = MarketReaderModule(self, reader_address)
        self.leverage_simulator = LeverageSimulatorModule(self, leverage_address)
        self.taxes = TaxesModule(self, taxes_address)
        self.agent = AgentIdentityModule(self)

    # ------------------------------------------------------------------
    # Gasless transaction helper
    # ------------------------------------------------------------------

    def send_transaction(self, function_call, value: int = 0) -> dict:
        """Build, sign, and send a transaction. Tries gasless (megafuel) first, falls back to regular RPC.

        Returns { 'hash': '0x...', 'receipt': {...} }
        """
        if not self.account:
            raise ValueError("Wallet (private_key) is required for write methods.")

        nonce = self.web3.eth.get_transaction_count(self.account.address)

        if self.gasless:
            # Try gasless: build with gas_price=0, send to megafuel
            try:
                tx = function_call.build_transaction({
                    'from': self.account.address,
                    'nonce': nonce,
                    'gasPrice': 0,
                    'value': value,
                })
                signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.account.key)
                raw_tx = '0x' + signed_tx.raw_transaction.hex()

                import requests as req
                resp = req.post(self.MEGAFUEL_RPC, json={
                    'jsonrpc': '2.0',
                    'method': 'eth_sendRawTransaction',
                    'params': [raw_tx],
                    'id': 1,
                })
                result = resp.json()

                if 'result' in result and not result.get('error'):
                    tx_hash_hex = result['result']
                    tx_hash = bytes.fromhex(tx_hash_hex[2:])
                    receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
                    return {'hash': tx_hash_hex, 'receipt': receipt}

                # Megafuel rejected — fall through to regular
            except Exception as e:
                logger.debug("Gasless attempt failed: %s", e)

        # Regular: build with real gasPrice, send via web3
        tx = function_call.build_transaction({
            'from': self.account.address,
            'nonce': nonce,
            'value': value,
        })
        signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        return {'hash': '0x' + tx_hash.hex(), 'receipt': receipt}

    # ------------------------------------------------------------------
    # Factory class method
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        rpc_url: str = DEFAULT_RPC_URL,
        private_key: Optional[str] = None,
        api_key: Optional[str] = None,
        **kwargs: Any,
    ) -> "BasisClient":
        """Create a fully-initialised :class:`BasisClient`.

        Compared to the plain constructor this additionally:

        1. Validates the RPC endpoint if a custom *rpc_url* is provided
           (must return chain-id 56 for BSC).
        2. Performs SIWE authentication when a *private_key* is supplied
           without an *api_key*.
        3. Auto-provisions an API key after successful authentication.

        Parameters
        ----------
        rpc_url:
            JSON-RPC endpoint URL. Defaults to the public BSC data-seed.
        private_key:
            Hex-encoded private key for signing transactions and SIWE.
        api_key:
            Pre-existing Basis API key (``bsk_...``).  When provided the
            SIWE auth step is skipped.
        **kwargs:
            Forwarded to :class:`BasisClient.__init__`.
        """
        client = cls(
            rpc_url=rpc_url,
            private_key=private_key,
            api_key=api_key,
            **kwargs,
        )

        # 1. Validate RPC if not using the default
        if rpc_url != DEFAULT_RPC_URL:
            client._validate_rpc()

        # 2. Fetch remote contract addresses and warn on mismatch
        try:
            import requests as _req
            res = _req.get(f"{client.api_domain}/contracts.json", timeout=5)
            if res.ok:
                remote = res.json()
                mismatched = [
                    name for name, default_addr in DEFAULT_ADDRESSES.items()
                    if name in remote and remote[name].lower() != default_addr.lower()
                ]
                if mismatched:
                    import warnings
                    warnings.warn(
                        "[basis-sdk] Contract addresses have changed. Please update your SDK to the latest version. "
                        f"Mismatched: {', '.join(mismatched)}",
                        stacklevel=2,
                    )
        except Exception:
            pass  # Remote unreachable — continue with hardcoded defaults

        # 3. Auth + key provisioning
        if private_key:
            client.authenticate()
            # Only provision an API key if one wasn't provided
            if not api_key:
                client.ensure_api_key()

        return client

    # ------------------------------------------------------------------
    # RPC validation
    # ------------------------------------------------------------------

    def _validate_rpc(self) -> None:
        """Check that the RPC endpoint is reachable and on BSC (chain 56)."""
        if not self.web3.is_connected():
            raise ConnectionError(
                f"Unable to connect to RPC endpoint: {self.rpc_url}"
            )
        chain_id = self.web3.eth.chain_id
        if chain_id != BSC_CHAIN_ID:
            raise ValueError(
                f"RPC returned chain ID {chain_id}, expected {BSC_CHAIN_ID} (BSC). "
                f"Please provide a valid BSC RPC URL."
            )
        logger.info("RPC validated: connected to BSC (chain ID %d).", chain_id)

    # ------------------------------------------------------------------
    # SIWE authentication
    # ------------------------------------------------------------------

    def authenticate(self) -> Dict[str, Any]:
        """Perform SIWE authentication to establish a cookie-based session.

        1. Fetch a nonce from the server.
        2. Build a SIWE message string.
        3. Sign it with the configured private key.
        4. POST the message + signature to ``/api/auth/verify``.

        The ``requests.Session`` inside :attr:`api` automatically stores the
        ``Set-Cookie`` header returned by the server so all subsequent
        session-authenticated requests work transparently.

        Returns the parsed JSON response from the verify endpoint.
        """
        if not self.account:
            raise ValueError("A private key is required to authenticate.")

        address = self.account.address

        # 1. Get nonce
        nonce_resp = self.api.get_nonce(address)
        nonce = nonce_resp["nonce"]

        # 2. Build SIWE message
        issued_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        message = (
            f"launchonbasis.com wants you to sign in with your Ethereum account:\n"
            f"{address}\n"
            f"\n"
            f"Sign in to Basis API.\n"
            f"\n"
            f"URI: {self.api_domain}\n"
            f"Version: 1\n"
            f"Chain ID: {BSC_CHAIN_ID}\n"
            f"Nonce: {nonce}\n"
            f"Issued At: {issued_at}"
        )

        # 3. Sign
        signable = encode_defunct(text=message)
        signed = self.account.sign_message(signable)
        signature = "0x" + signed.signature.hex()

        # 4. Verify
        result = self.api.verify(message, signature)
        logger.info("SIWE authentication successful for %s.", address)
        return result

    # ------------------------------------------------------------------
    # API key management helpers
    # ------------------------------------------------------------------

    def ensure_api_key(self) -> str:
        """Ensure an API key is available.

        * If an API key was already provided via the constructor, returns it.
        * If the server reports an existing key, the SDK cannot retrieve the
          plaintext (only a masked hint is returned). Raises an error
          instructing the operator to supply the key.
        * If no keys exist yet, a new one is created. The key is only returned
          **once** at creation time — store it securely for future runs.

        Returns the API key string.
        """
        # Already have a key (passed via constructor or prior call)
        if self.api_key:
            return self.api_key

        keys_resp = self.api.list_api_keys()
        keys = keys_resp.get("keys", [])

        if keys:
            # A key exists but we can't retrieve the plaintext
            raise RuntimeError(
                "An API key already exists for this wallet but the full key cannot be "
                "retrieved (the server only returns a masked hint). Pass your API key "
                'via the api_key option when creating the client, e.g.: '
                'BasisClient.create(private_key=..., api_key="bsk_...")'
            )

        # No keys exist — create one
        create_resp = self.api.create_api_key(label="basis-sdk-auto")
        self.api_key = create_resp["key"]

        logger.warning(
            "New API key created: %s — Save this key, it cannot be retrieved again. "
            "Pass it via the api_key option on future runs.",
            self.api_key,
        )

        return self.api_key

    # ------------------------------------------------------------------
    # Session helpers
    # ------------------------------------------------------------------

    def get_session(self, address: Optional[str] = None) -> Dict[str, Any]:
        """Return current session info.

        Wraps ``GET /api/auth/me``.
        """
        return self.api.get_me(address=address)

    def logout(self) -> Dict[str, Any]:
        """Log out the current session.

        Wraps ``DELETE /api/auth/me``.
        """
        if not self.account:
            raise ValueError("No account is associated with this client.")
        return self.api.logout(self.account.address)

    def claim_faucet(self, referrer: Optional[str] = None) -> Dict[str, Any]:
        """Claim daily USDB from the faucet via the server API.

        Amount depends on active signals (max 500 USDB/day, 24h cooldown).
        Requires SIWE session — call :meth:`authenticate` first.

        Convenience wrapper around ``client.api.claim_faucet()``.

        Parameters
        ----------
        referrer : str, optional
            Referrer wallet address for the referral system.
        """
        return self.api.claim_faucet(referrer=referrer)
