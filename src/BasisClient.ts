import { createPublicClient, createWalletClient, http, custom, PublicClient, WalletClient, Address } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { bsc } from 'viem/chains';
import { SiweMessage } from 'siwe';

const MEGAFUEL_RPC = 'https://bsc-megafuel.nodereal.io/';

/**
 * Creates a viem transport that tries gasless (megafuel) first for sends,
 * falls back to regular RPC on failure. Reads always go to regular RPC.
 */
function createGaslessTransport(rpcUrl: string) {
  const regularTransport = http(rpcUrl);

  return custom({
    async request({ method, params }) {
      const transport = regularTransport({ chain: bsc });

      if (method === 'eth_sendRawTransaction') {
        // Try megafuel first (the tx is signed with gasPrice: 0)
        try {
          const megafuelRes = await fetch(MEGAFUEL_RPC, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ jsonrpc: '2.0', method, params, id: 1 }),
          });
          const megafuelResult = await megafuelRes.json();
          if (!megafuelResult.error) {
            return megafuelResult.result;
          }
        } catch {}

        // Megafuel rejected — throw a specific error so the SDK can catch and retry
        const err = new Error('GASLESS_REJECTED');
        (err as any).code = 'GASLESS_REJECTED';
        throw err;
      }

      // For gasPrice: return 0 so viem signs the tx with zero gas
      if (method === 'eth_gasPrice') {
        return '0x0';
      }

      // For maxFeePerGas queries: also return 0
      if (method === 'eth_maxPriorityFeePerGas') {
        return '0x0';
      }

      // Everything else goes to regular RPC
      return transport.request({ method, params } as any);
    },
  });
}

import { BasisAPI } from './api';
import { FactoryModule } from './modules/Factory';
import { TradingModule } from './modules/Trading';
import { PredictionMarketsModule } from './modules/PredictionMarkets';
import { OrderBookModule } from './modules/OrderBook';
import { LoansModule } from './modules/Loans';
import { VestingModule } from './modules/Vesting';
import { StakingModule } from './modules/Staking';
import { MarketResolverModule } from './modules/MarketResolver';
import { PrivateMarketsModule } from './modules/PrivateMarkets';
import { MarketReaderModule } from './modules/MarketReader';
import { LeverageSimulatorModule } from './modules/LeverageSimulator';
import { TaxesModule } from './modules/Taxes';
import { AgentIdentityModule, AgentConfig } from './modules/AgentIdentity';

export interface BasisClientOptions {
  rpcUrl?: string;
  privateKey?: `0x${string}`;
  apiKey?: string;
  apiDomain?: string;
  gasless?: boolean;

  // Contract Addresses
  factoryAddress?: Address;
  swapAddress?: Address;
  marketTradingAddress?: Address;
  loanHubAddress?: Address;
  vestingAddress?: Address;
  stakingAddress?: Address;
  resolverAddress?: Address;
  privateMarketAddress?: Address;
  readerAddress?: Address;
  leverageAddress?: Address;
  taxesAddress?: Address;

  // Token Addresses
  usdbAddress?: Address;
  mainTokenAddress?: Address;

  // ERC-8004 Agent Identity
  agent?: boolean | AgentConfig;
}

// Hardcoded defaults — used as fallback when the remote contracts.json is unreachable
const DEFAULT_ADDRESSES = {
  factory: '0xB6BA282f29A7C67059f4E9D0898eE58f5C79960D',
  swap: '0x9F9cF98F68bDbCbC5cf4c6402D53cEE1D180715f',
  marketTrading: '0x396216fc9d2c220afD227B59097cf97B7dEaCb57',
  loanHub: '0xFe19644d52fD0014EBa40c6A8F4Bfee4Ce3B2449',
  vesting: '0xedd987c7723B9634b0Aa6161258FED3e89F9094C',
  usdb: '0x42bcF288e51345c6070F37f30332ee5090fC36BF',
  mainToken: '0x3067ce754a36d0a2A1b215C4C00315d9Da49EF15',
  staking: '0x1FE7189270fb93c32a1fEfA71d1795c05C41cb33',
  resolver: '0xB5FFCCB422531Cf462ec430170f85d8dD3dC3f57',
  privateMarket: '0x28675A82ee3c2e6d2C85887Ea587FbDD3E3C86EE',
  reader: '0xF406cA6403c57Ad04c8E13F4ae87b3732daa087d',
  leverage: '0xeffb140d821c5B20EFc66346Cf414EeAC8A8FDB2',
  taxes: '0x4501d1279273c44dA483842ED17b5451e7d3A601',
} as const;

export class BasisClient {
  public publicClient: PublicClient;
  public walletClient?: WalletClient;
  private _fallbackWalletClient?: WalletClient;
  public apiDomain: string;
  public usdbAddress: Address;
  public mainTokenAddress: Address;

  // API wrapper
  public api: BasisAPI;

  // Modules
  public factory: FactoryModule;
  public trading: TradingModule;
  public predictionMarkets: PredictionMarketsModule;
  public orderBook: OrderBookModule;
  public loans: LoansModule;
  public vesting: VestingModule;
  public staking: StakingModule;
  public resolver: MarketResolverModule;
  public privateMarkets: PrivateMarketsModule;
  public marketReader: MarketReaderModule;
  public leverageSimulator: LeverageSimulatorModule;
  public taxes: TaxesModule;
  public agent: AgentIdentityModule;

  // Auth state
  private _sessionCookie: string | null = null;
  private _apiKey: string | null = null;

  /**
   * Write a contract call with automatic gasless fallback.
   * Tries megafuel (gasless) first. If rejected, retries with regular RPC.
   */
  async writeContract(request: any): Promise<`0x${string}`> {
    if (!this.walletClient) throw new Error('Wallet required for write operations.');

    try {
      return await this.walletClient.writeContract(request);
    } catch (e: any) {
      // If gasless was rejected and we have a fallback, retry with regular gas
      if (this._fallbackWalletClient && (
        e.code === 'GASLESS_REJECTED' ||
        e.message?.includes('GASLESS_REJECTED') ||
        e.details?.includes('GASLESS_REJECTED')
      )) {
        return await this._fallbackWalletClient.writeContract(request);
      }
      throw e;
    }
  }

  /** Session cookie for authenticated API requests. */
  get sessionCookie(): string | null {
    return this._sessionCookie;
  }

  /** API key for v1 data endpoints. */
  get apiKey(): string | null {
    return this._apiKey;
  }

  constructor(options: BasisClientOptions = {}) {
    const rpcUrl = options.rpcUrl || 'https://bsc-dataseed.binance.org/';
    this.apiDomain = options.apiDomain || 'https://launchonbasis.com';

    this.publicClient = createPublicClient({
      chain: bsc,
      transport: http(rpcUrl),
    });

    if (options.privateKey) {
      const account = privateKeyToAccount(options.privateKey);
      const gasless = options.gasless !== false; // default: true

      if (gasless) {
        // Primary: gasless transport (megafuel)
        this.walletClient = createWalletClient({
          account,
          chain: bsc,
          transport: createGaslessTransport(rpcUrl),
        });
        // Fallback: regular RPC for when megafuel rejects
        this._fallbackWalletClient = createWalletClient({
          account,
          chain: bsc,
          transport: http(rpcUrl),
        });
      } else {
        this.walletClient = createWalletClient({
          account,
          chain: bsc,
          transport: http(rpcUrl),
        });
      }
    }

    if (options.apiKey) {
      this._apiKey = options.apiKey;
    }

    // Default addresses — can be overridden by options or remote contracts.json
    const factoryAddr = options.factoryAddress || DEFAULT_ADDRESSES.factory;
    const swapAddr = options.swapAddress || DEFAULT_ADDRESSES.swap;
    const marketTradingAddr = options.marketTradingAddress || DEFAULT_ADDRESSES.marketTrading;
    const loanHubAddr = options.loanHubAddress || DEFAULT_ADDRESSES.loanHub;
    const vestingAddr = options.vestingAddress || DEFAULT_ADDRESSES.vesting;
    this.usdbAddress = options.usdbAddress || DEFAULT_ADDRESSES.usdb;
    this.mainTokenAddress = options.mainTokenAddress || DEFAULT_ADDRESSES.mainToken;

    this.api = new BasisAPI(this);
    this.factory = new FactoryModule(this, factoryAddr);
    this.trading = new TradingModule(this, swapAddr);
    this.predictionMarkets = new PredictionMarketsModule(this, marketTradingAddr);
    this.orderBook = new OrderBookModule(this, marketTradingAddr);
    this.loans = new LoansModule(this, loanHubAddr);
    this.vesting = new VestingModule(this, vestingAddr);
    this.staking = new StakingModule(this, options.stakingAddress || DEFAULT_ADDRESSES.staking);
    this.resolver = new MarketResolverModule(this, options.resolverAddress || DEFAULT_ADDRESSES.resolver);
    this.privateMarkets = new PrivateMarketsModule(this, options.privateMarketAddress || DEFAULT_ADDRESSES.privateMarket);
    this.marketReader = new MarketReaderModule(this, options.readerAddress || DEFAULT_ADDRESSES.reader);
    this.leverageSimulator = new LeverageSimulatorModule(this, options.leverageAddress || DEFAULT_ADDRESSES.leverage);
    this.taxes = new TaxesModule(this, options.taxesAddress || DEFAULT_ADDRESSES.taxes);
    this.agent = new AgentIdentityModule(this);
  }

  /**
   * Async factory method that creates a fully initialized BasisClient.
   *
   * - Validates custom RPC URL by checking chainId === 56 (BSC).
   * - If a privateKey is provided and no apiKey: authenticates via SIWE and auto-provisions an API key.
   * - If an apiKey is provided: stores it directly.
   */
  static async create(options: BasisClientOptions = {}): Promise<BasisClient> {
    const client = new BasisClient(options);

    // Validate custom RPC if provided
    if (options.rpcUrl) {
      try {
        const chainId = await client.publicClient.getChainId();
        if (chainId !== 56) {
          throw new Error(
            `RPC endpoint returned chainId ${chainId}, expected 56 (BSC Mainnet). ` +
            `Ensure your RPC URL points to BSC Mainnet.`
          );
        }
      } catch (err: any) {
        if (err.message && err.message.includes('chainId')) {
          throw err;
        }
        throw new Error(
          `Failed to validate RPC endpoint "${options.rpcUrl}": ${err.message || err}. ` +
          `Check that the URL is correct and the node is reachable.`
        );
      }
    }

    // If privateKey provided, always do SIWE auth (some endpoints require session)
    if (options.privateKey) {
      if (!client.walletClient?.account) {
        throw new Error('WalletClient was not initialized despite privateKey being provided.');
      }
      const address = client.walletClient.account.address;
      await client.authenticate(address);
      // Only provision an API key if one wasn't provided
      if (!options.apiKey) {
        await client.ensureApiKey();
      }
    }

    // Fetch remote contract addresses and warn on mismatch
    try {
      const res = await fetch(`${client.apiDomain}/contracts.json`);
      if (res.ok) {
        const remote = await res.json();
        const mapping: [string, string, string][] = [
          ['factory', remote.factory, DEFAULT_ADDRESSES.factory],
          ['swap', remote.swap, DEFAULT_ADDRESSES.swap],
          ['marketTrading', remote.marketTrading, DEFAULT_ADDRESSES.marketTrading],
          ['loanHub', remote.loanHub, DEFAULT_ADDRESSES.loanHub],
          ['vesting', remote.vesting, DEFAULT_ADDRESSES.vesting],
          ['usdb', remote.usdb, DEFAULT_ADDRESSES.usdb],
          ['mainToken', remote.mainToken, DEFAULT_ADDRESSES.mainToken],
          ['staking', remote.staking, DEFAULT_ADDRESSES.staking],
          ['resolver', remote.resolver, DEFAULT_ADDRESSES.resolver],
          ['privateMarket', remote.privateMarket, DEFAULT_ADDRESSES.privateMarket],
          ['reader', remote.reader, DEFAULT_ADDRESSES.reader],
          ['leverage', remote.leverage, DEFAULT_ADDRESSES.leverage],
          ['taxes', remote.taxes, DEFAULT_ADDRESSES.taxes],
        ];
        const mismatched = mapping.filter(([, remoteAddr, defaultAddr]) =>
          remoteAddr && remoteAddr.toLowerCase() !== defaultAddr.toLowerCase()
        );
        if (mismatched.length > 0) {
          console.warn(
            `[basis-sdk] Contract addresses have changed. Please update your SDK to the latest version.\n` +
            `Mismatched: ${mismatched.map(([name]) => name).join(', ')}`
          );
        }
      }
    } catch {
      // Remote unreachable — continue with hardcoded defaults
    }

    // ERC-8004 Agent Identity registration
    if (options.agent && options.privateKey) {
      const agentConfig = typeof options.agent === 'object' ? options.agent : undefined;
      try {
        await client.agent.registerAndSync(agentConfig);
      } catch (err) {
        console.warn('Agent registration warning:', err instanceof Error ? err.message : err);
      }
    }

    return client;
  }

  /**
   * Authenticates with the Basis API using Sign-In with Ethereum (SIWE).
   *
   * 1. Fetches a nonce from the server
   * 2. Constructs and signs a SIWE message
   * 3. Submits the signed message for verification
   * 4. Stores the session cookie for subsequent authenticated requests
   */
  async authenticate(address: `0x${string}`): Promise<void> {
    if (!this.walletClient) {
      throw new Error('WalletClient must be initialized to authenticate. Provide a privateKey.');
    }

    // 1. Fetch nonce
    const nonceRes = await fetch(
      `${this.apiDomain}/api/auth/nonce?address=${address}`
    );
    if (!nonceRes.ok) {
      throw new Error(`Failed to fetch nonce: ${nonceRes.status} ${nonceRes.statusText}`);
    }
    const nonceData = await nonceRes.json();
    const nonce: string = nonceData.nonce;

    // 2. Build SIWE message
    const domain = new URL(this.apiDomain).host;
    const message = new SiweMessage({
      domain,
      address,
      statement: 'Sign in to Basis API.',
      uri: this.apiDomain,
      version: '1',
      chainId: 56,
      nonce,
    });
    const preparedMessage = message.prepareMessage();

    // 3. Sign the message
    const signature = await this.walletClient.signMessage({
      account: this.walletClient.account!,
      message: preparedMessage,
    });

    // 4. Verify with backend
    const verifyRes = await fetch(`${this.apiDomain}/api/auth/verify`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: preparedMessage, signature }),
    });

    if (!verifyRes.ok) {
      const body = await verifyRes.text().catch(() => '');
      throw new Error(
        `SIWE verification failed: ${verifyRes.status} ${verifyRes.statusText}. ${body}`
      );
    }

    // 5. Extract session cookie from Set-Cookie header
    const setCookie = verifyRes.headers.get('set-cookie');
    if (setCookie) {
      this._sessionCookie = setCookie;
    }
  }

  /**
   * Ensures an API key is available.
   *
   * - If an API key was already provided via the constructor, this is a no-op.
   * - If the server reports an existing key, the SDK cannot retrieve the
   *   plaintext (only a masked hint is returned). In that case an error is
   *   thrown instructing the operator to supply the key.
   * - If no keys exist yet, a new one is created. The key is only returned
   *   **once** at creation time — store it securely for future runs.
   *
   * @returns The API key string.
   */
  async ensureApiKey(): Promise<string> {
    // Already have a key (passed via constructor or prior call)
    if (this._apiKey) {
      return this._apiKey;
    }

    if (!this._sessionCookie) {
      throw new Error('No session cookie. Call authenticate() first.');
    }

    // Check for existing keys on the server
    const listRes = await fetch(`${this.apiDomain}/api/v1/auth/keys`, {
      headers: { Cookie: this._sessionCookie },
    });
    if (!listRes.ok) {
      throw new Error(`Failed to list API keys: ${listRes.status} ${listRes.statusText}`);
    }
    const listData = await listRes.json();

    if (listData.keys && listData.keys.length > 0) {
      // A key exists but we can't retrieve the plaintext — operator must supply it
      throw new Error(
        'An API key already exists for this wallet but the full key cannot be retrieved ' +
        '(the server only returns a masked hint). Pass your API key via the apiKey option ' +
        'when creating the client, e.g.: BasisClient.create({ privateKey, apiKey: "bsk_..." })'
      );
    }

    // No keys exist — create one
    const createRes = await fetch(`${this.apiDomain}/api/v1/auth/keys`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Cookie: this._sessionCookie,
      },
      body: JSON.stringify({ label: 'basis-sdk-auto' }),
    });
    if (!createRes.ok) {
      const body = await createRes.text().catch(() => '');
      throw new Error(
        `Failed to create API key: ${createRes.status} ${createRes.statusText}. ${body}`
      );
    }
    const createData = await createRes.json();
    const newKey: string = createData.key;
    this._apiKey = newKey;

    console.warn(
      `[basis-sdk] New API key created: ${newKey}\n` +
      `Save this key — it cannot be retrieved again. Pass it via the apiKey option on future runs.`
    );

    return newKey;
  }

  /**
   * Returns the current session status.
   * Optionally checks for a specific address.
   */
  async getSession(address?: string): Promise<{
    isLoggedIn: boolean;
    address?: string;
    addresses?: string[];
    allAddresses?: string[];
  }> {
    const params = address ? `?address=${address}` : '';
    const headers: Record<string, string> = {};
    if (this._sessionCookie) {
      headers['Cookie'] = this._sessionCookie;
    }
    const res = await fetch(`${this.apiDomain}/api/auth/me${params}`, { headers });
    if (!res.ok) {
      throw new Error(`Failed to get session: ${res.status} ${res.statusText}`);
    }
    return res.json();
  }

  /**
   * Logs out the specified address, removing it from the session.
   */
  async logout(address: string): Promise<{ ok: boolean; message: string }> {
    if (!this._sessionCookie) {
      throw new Error('No session cookie. Not logged in.');
    }
    const res = await fetch(`${this.apiDomain}/api/auth/me?address=${address}`, {
      method: 'DELETE',
      headers: { Cookie: this._sessionCookie },
    });
    if (!res.ok) {
      throw new Error(`Logout failed: ${res.status} ${res.statusText}`);
    }
    const data = await res.json();
    this._sessionCookie = null;
    this._apiKey = null;
    return data;
  }

  /**
   * Claims daily USDB from the faucet via the server API.
   * Amount depends on active signals (max 500 USDB/day, 24h cooldown).
   * Requires SIWE session — call authenticate() first.
   *
   * Convenience wrapper around `client.api.claimFaucet()`.
   *
   * @param referrer - Optional referrer wallet address for the referral system.
   */
  async claimFaucet(referrer?: string): Promise<{
    success: boolean;
    amount: number;
    txHash: string;
    signals: {
      base: boolean;
      twitter: boolean;
      active: boolean;
      hatchling: boolean;
      tidal: boolean;
    };
  }> {
    return this.api.claimFaucet(referrer);
  }
}
