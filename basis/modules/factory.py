import json
import logging
import os
from pathlib import Path
from web3 import Web3

logger = logging.getLogger(__name__)

def load_abi(filename: str):
    abi_path = Path(__file__).parent.parent / 'abis' / filename
    with open(abi_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        return data.get('abi', [])

class FactoryModule:
    def __init__(self, client, factory_address: str):
        self.client = client
        self.factory_address = Web3.to_checksum_address(factory_address)
        self.factory_abi = load_abi('ATokenFactory.json')
        self.token_abi = load_abi('FACTORYTOKEN.json')
        self.contract = self.client.web3.eth.contract(address=self.factory_address, abi=self.factory_abi)

    def _sync_tx(self, tx_hash: str):
        """Sync tx to backend. Raises on failure."""
        if not tx_hash.startswith("0x"):
            tx_hash = "0x" + tx_hash
        self.client.api.sync_transaction(tx_hash)

    def _create_token(
        self,
        symbol: str,
        name: str,
        hybrid_multiplier: int,
        frozen: bool,
        usdb_for_bonding: int,
        start_lp: int,
        auto_vest: bool,
        auto_vest_duration: int,
        gradual_autovest: bool
    ):
        fee_amount = self.contract.functions.feeAmount().call()
        func = self.contract.functions.createToken(
            symbol,
            name,
            hybrid_multiplier,
            frozen,
            usdb_for_bonding,
            start_lp,
            auto_vest,
            auto_vest_duration,
            gradual_autovest
        )
        return self.client.send_transaction(func, value=fee_amount)

    def create_token_with_metadata(
        self,
        symbol: str,
        name: str,
        hybrid_multiplier: int,
        start_lp: int,
        image_url: str = None,
        image_file: str = None,
        description: str = None,
        website: str = None,
        telegram: str = None,
        twitterx: str = None,
        frozen: bool = False,
        usdb_for_bonding: int = 0,
        auto_vest: bool = False,
        auto_vest_duration: int = 0,
        gradual_autovest: bool = False,
    ):
        """Creates a token and registers its metadata on IPFS in one call.

        Requires SIWE authentication (call client.authenticate() first).

        1. Creates the token on-chain
        2. Parses the new token address from logs
        3. Downloads, resizes (512x512 WebP), and uploads the image to IPFS
        4. Creates metadata on IPFS

        Args:
            hybrid_multiplier: raw integer (not wei) -- controls floor price rise speed
            start_lp: initial liquidity in wei (18 decimals)
            usdb_for_bonding: USDB amount in wei (18 decimals)
            auto_vest_duration: vesting duration in days (integer)

        Returns dict with hash, receipt, token_address, image_url, metadata.
        """
        # 1. Create token on-chain
        create_result = self._create_token(
            symbol=symbol, name=name,
            hybrid_multiplier=hybrid_multiplier, frozen=frozen,
            usdb_for_bonding=usdb_for_bonding, start_lp=start_lp,
            auto_vest=auto_vest, auto_vest_duration=auto_vest_duration,
            gradual_autovest=gradual_autovest,
        )

        receipt = create_result['receipt']
        if receipt.get('status') == 0:
            raise RuntimeError(f"Token creation reverted (tx: {create_result['hash']})")

        # 2. Parse token address from TokenCreated event
        token_created_topic = Web3.keccak(text="TokenCreated(address,string,string,address)").hex()
        factory_lower = self.factory_address.lower()
        token_address = None
        for log_entry in receipt.get('logs', []):
            addr = log_entry.get('address', '')
            if addr.lower() != factory_lower:
                continue
            topics = log_entry.get('topics', [])
            if not topics:
                continue
            t0 = topics[0].hex() if isinstance(topics[0], bytes) else str(topics[0])
            if t0 == token_created_topic and len(topics) > 1:
                raw = topics[1].hex() if isinstance(topics[1], bytes) else str(topics[1])
                token_address = Web3.to_checksum_address("0x" + raw[-40:])
                break

        if not token_address:
            raise RuntimeError("Could not extract token address from creation logs.")

        # 3. Upload image
        if not image_url and not image_file:
            raise ValueError('Either image_url or image_file is required.')
        if image_file:
            uploaded_image_url = self.client.api.upload_image(image_file, purpose='token', address=token_address)
        else:
            uploaded_image_url = self.client.api.upload_image_from_url(image_url, contract_address=token_address)

        # 4. Create metadata on IPFS
        metadata = self.client.api.update_metadata(
            address=token_address,
            description=description,
            image=uploaded_image_url,
            website=website,
            telegram=telegram,
            twitterx=twitterx,
        )

        result = {
            'hash': create_result['hash'],
            'receipt': receipt,
            'token_address': token_address,
            'image_url': uploaded_image_url,
            'metadata': metadata,
        }
        self._sync_tx(result['hash'])
        return result

    def disable_freeze(self, token_address: str):
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        func = token_contract.functions.DisableFreeze()
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def set_whitelisted_wallet(self, token_address: str, wallets: list[str], amount: int, tag: str):
        """Sets whitelisted wallets for a token.

        Args:
            amount: token amount in wei (18 decimals)
        """
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        checksum_wallets = [Web3.to_checksum_address(w) for w in wallets]
        func = token_contract.functions.SetWhitelistedWallet(checksum_wallets, amount, tag)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def is_ecosystem_token(self, token_address: str) -> bool:
        """Checks if a token is an ecosystem token."""
        return self.contract.functions.isEcosystemToken(Web3.to_checksum_address(token_address)).call()

    def get_tokens_by_creator(self, creator: str) -> list:
        """Returns all tokens created by a given address."""
        return self.contract.functions.getTokensByCreator(Web3.to_checksum_address(creator)).call()

    def get_fee_amount(self) -> int:
        """Returns the current fee amount for token creation."""
        return self.contract.functions.feeAmount().call()

    def remove_whitelist(self, token_address: str, wallet: str):
        """Removes a wallet from a token's whitelist."""
        checksum_token = Web3.to_checksum_address(token_address)
        checksum_wallet = Web3.to_checksum_address(wallet)
        token_contract = self.client.web3.eth.contract(address=checksum_token, abi=self.token_abi)
        func = token_contract.functions.RemoveWhitelist(checksum_wallet)
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def claim_rewards(self, token_address: str):
        """Claim accumulated USDB rewards from presale shares on a factory token."""
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        func = token_contract.functions.claimRewards()
        result = self.client.send_transaction(func)
        self._sync_tx(result['hash'])
        return result

    def get_floor_price(self, token_address: str) -> int:
        """Returns the floor price of a factory token in USDB. Only for factory tokens, not STASIS."""
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        return token_contract.functions.calculateFloor().call()

    def get_claimable_rewards(self, token_address: str, investor: str) -> int:
        """Get claimable USDB rewards for an address on a factory token."""
        checksum_addr = Web3.to_checksum_address(token_address)
        checksum_investor = Web3.to_checksum_address(investor)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        return token_contract.functions.getClaimableRewards(checksum_investor).call()

    def get_token_state(self, token_address: str):
        checksum_addr = Web3.to_checksum_address(token_address)
        token_contract = self.client.web3.eth.contract(address=checksum_addr, abi=self.token_abi)
        
        frozen = token_contract.functions.frozen().call()
        has_bonded = token_contract.functions.hasBonded().call()
        total_supply = token_contract.functions.totalSupply().call()
        usd_price = token_contract.functions.getUSDPrice().call()

        return {
            'frozen': frozen,
            'hasBonded': has_bonded,
            'totalSupply': str(total_supply),
            'usdPrice': str(usd_price)
        }
