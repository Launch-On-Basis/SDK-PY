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
        gasless: bool = True,
        factory_address: str = "0x13b32CcB24F1fd070cE8Ee5EA83AAC5a60f853DA",
        swap_address: str = "0xD9C99E3E92c5Cb303371223FAaA3C8f5FeE39399",
        market_trading_address: str = "0xcf8368E674A13662BA55F98bdb9A6FBC6aCEbEeE",
        loan_hub_address: str = "0x4d3ca2DA5F77FA8c0D0CA53b4078D025519b6d8f",
        vesting_address: str = "0xd27d9999b360f1D9c1Fb88F91d038D9d674f127b",
        usdb_address: str = "0x1b2b5D36e5F07BD6a272F95079590B70AdB776b1",
        main_token_address: str = "0x4B01013aC1F3501c64DFC7bC08aE5E23F391b5EA",
        staking_address: str = "0xb956d467D95a16f660aaBF25c5dE81A897254332",
        resolver_address: str = "0xDCE6daaE48Ec55977D22BB9D855BF7ef222077cf",
        private_market_address: str = "0xe9aA86286bE3b353241091910FB11Fd62CC88bd3",
        reader_address: str = "0x320C73CD00Dd484b53140795F9eD1C875A5A6D99",
        leverage_address: str = "0xD10B597d2B5CDAf965f7AC29339866513311e84d",
        taxes_address: str = "0xb65Ff977fFb0ABa34c28e8b571D29DFb1a3416a4",
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
            # Try gasless: build with gasPrice=0, send to megafuel
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

        # 2. Auth + key provisioning
        if private_key and not api_key:
            client.authenticate()
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
        """Ensure an API key exists, creating one if necessary.

        After this call :pyattr:`api_key` is guaranteed to be set.

        Returns the API key string.
        """
        keys_resp = self.api.list_api_keys()
        keys = keys_resp.get("keys", [])

        if keys and keys[0].get("key"):
            self.api_key = keys[0]["key"]
            logger.info("Using existing API key: %s...", self.api_key[:12])
        else:
            # Delete existing key with null value before creating new one
            if keys and not keys[0].get("key"):
                self.api.delete_api_key(keys[0]["id"])
            create_resp = self.api.create_api_key(label="basis-sdk-auto")
            self.api_key = create_resp["key"]
            logger.info("Created new API key: %s...", self.api_key[:12])

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

    def claim_faucet(self, referrer: str = "0x0000000000000000000000000000000000000000") -> Dict[str, Any]:
        """Claim 10,000 test USDB from the faucet. One claim per wallet, ever.

        USDB from faucet is non-transferable except to Basis protocol contracts.
        Optionally pass a referrer address for the referral system.
        """
        if not self.account:
            raise ValueError("Wallet (private_key) is required to claim faucet.")

        faucet_abi = [{"inputs": [{"name": "_referrer", "type": "address"}], "name": "faucet", "outputs": [], "stateMutability": "nonpayable", "type": "function"}]
        usdb_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(self.usdb_address), abi=faucet_abi
        )

        func = usdb_contract.functions.faucet(Web3.to_checksum_address(referrer))
        tx = func.build_transaction({
            'from': self.account.address,
            'nonce': self.web3.eth.get_transaction_count(self.account.address),
        })
        signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)

        return {'hash': "0x" + tx_hash.hex(), 'receipt': receipt}

    def set_referrer(self, referrer: str) -> Dict[str, Any]:
        """Set a referrer for the current wallet. One-time only — reverts if already set.

        Use this if you didn't pass a referrer during claim_faucet().
        """
        if not self.account:
            raise ValueError("Wallet (private_key) is required.")

        abi = [{"inputs": [{"name": "_referrer", "type": "address"}], "name": "setReferrer", "outputs": [], "stateMutability": "nonpayable", "type": "function"}]
        usdb_contract = self.web3.eth.contract(
            address=Web3.to_checksum_address(self.usdb_address), abi=abi
        )

        func = usdb_contract.functions.setReferrer(Web3.to_checksum_address(referrer))
        tx = func.build_transaction({
            'from': self.account.address,
            'nonce': self.web3.eth.get_transaction_count(self.account.address),
        })
        signed_tx = self.web3.eth.account.sign_transaction(tx, private_key=self.account.key)
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)

        return {'hash': "0x" + tx_hash.hex(), 'receipt': receipt}
