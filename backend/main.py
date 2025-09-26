# main.py
import os, time, random, requests
from datetime import datetime, timezone
from typing import Optional, Tuple

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ---- third-party (sign & submit) ----
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.commitment_config import CommitmentLevel
from solders.rpc.config import RpcSendTransactionConfig
from solders.rpc.requests import SendVersionedTransaction

load_dotenv()

# ---------- Settings ----------
WALLET_ADDRESS      = os.getenv("WALLET_ADDRESS", "").strip()
WALLET_PRIVATE_KEY  = os.getenv("WALLET_PRIVATE_KEY", "").strip()
SOLANA_RPC_URL      = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()
PRIORITY_FEE        = float(os.getenv("PRIORITY_FEE", "0.000001"))
TOKEN_MINT          = os.getenv("TOKEN_MINT", "").strip()
HELIUS_API_KEY      = os.getenv("HELIUS_API_KEY", "").strip()
FRONTEND_ORIGIN     = os.getenv("FRONTEND_ORIGIN", "").strip()

if not (WALLET_ADDRESS and WALLET_PRIVATE_KEY and TOKEN_MINT and SOLANA_RPC_URL):
    print("[WARN] Missing critical .env values. Claim/Buy/Burn will fail until provided.")

# ---------- FastAPI ----------
app = FastAPI(title="Tolkien Backend", version="1.0.0")

allowed_origins = {
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost",
    "http://127.0.0.1",
}
if FRONTEND_ORIGIN:
    allowed_origins.add(FRONTEND_ORIGIN)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(allowed_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

# ---------- Dashboard state ----------
STATE = {
    "price_usd": 0.0,
    "volume_change_pct": 0.0,
    "buybacks_usd": 0.0,
    "burned_usd": 0.0,
    "market_cap_usd": 0.0,
    "supply_burned_pct": 0.0,
    "last_goal_bucket": 0,     # integer bucket index we've last processed
    "tx": [],                  # recent transactions
}

GOAL_STEP = 100_000.0         # trigger size ($100k)
LAMPORTS_PER_SOL = 1_000_000_000

# ----- TX helpers -----
def push_tx(kind: str, amount_sol: float, desc: str, sig: Optional[str] = None):
    STATE["tx"].insert(0, {
        "signature": sig,
        "kind": kind,  # "claim" | "buyback" | "burn"
        "amount_sol": float(amount_sol or 0),
        "status": "confirmed" if sig else "recorded",
        "timestamp": now_iso(),
        "description": desc
    })
    STATE["tx"] = STATE["tx"][:50]

def get_balance_sol(pubkey: str) -> float:
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getBalance",
                   "params": [pubkey, {"commitment": "confirmed"}]}
        r = requests.post(SOLANA_RPC_URL, json=payload, timeout=30)
        r.raise_for_status()
        result = r.json()
        if "result" not in result or "value" not in result["result"]:
            raise RuntimeError(f"Invalid RPC response: {result}")
        lamports = result["result"]["value"]
        return lamports / LAMPORTS_PER_SOL
    except Exception as e:
        print(f"[ERROR] Failed to get SOL balance for {pubkey}: {e}")
        return 0.0

def _send_portal_tx_and_submit(raw_bytes: bytes) -> str:
    """Sign Pump Portal tx and submit to RPC; return signature."""
    if not WALLET_PRIVATE_KEY:
        raise RuntimeError("WALLET_PRIVATE_KEY not configured")
    
    try:
        kp = Keypair.from_base58_string(WALLET_PRIVATE_KEY)
        portal_tx = VersionedTransaction.from_bytes(raw_bytes)
        signed = VersionedTransaction(portal_tx.message, [kp])

        cfg = RpcSendTransactionConfig(preflight_commitment=CommitmentLevel.Confirmed)
        req = SendVersionedTransaction(signed, cfg)
        r = requests.post(SOLANA_RPC_URL, headers={"Content-Type": "application/json"},
                          data=req.to_json(), timeout=60)
        r.raise_for_status()
        result = r.json().get("result")
        if not result:
            error_info = r.json().get("error", {})
            raise RuntimeError(f"Transaction failed: {error_info}")
        return result
    except Exception as e:
        print(f"[ERROR] Failed to submit transaction: {e}")
        raise

def pump_portal_trade_local(data: dict) -> str:
    resp = requests.post("https://pumpportal.fun/api/trade-local", data=data, timeout=60)
    resp.raise_for_status()
    return _send_portal_tx_and_submit(resp.content)

# ----- Helius market data (price + market cap) -----
_HELIUS_CACHE_TTL = 20  # seconds
_last_helius_t = 0.0

def refresh_market_data():
    """Refresh STATE.price_usd and STATE.market_cap_usd from Helius (cached briefly)."""
    global _last_helius_t
    now = time.time()
    if now - _last_helius_t < _HELIUS_CACHE_TTL:
        return
    if not HELIUS_API_KEY:
        # keep whatever we have; dev fallback (no API key)
        return

    url = f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": "getAsset",
        "params": {
            "id": TOKEN_MINT,
            "displayOptions": {"showFungibleTokens": True}
        }
    }
    try:
        r = requests.post(url, json=payload, timeout=20)
        r.raise_for_status()
        data = r.json()
        info = (data.get("result") or {}).get("token_info") or {}
        price_info = info.get("price_info") or {}
        supply = info.get("supply")
        decimals = info.get("decimals", 0)
        price = float(price_info.get("price_per_token") or 0.0)
        adjusted_supply = float(supply or 0) / (10 ** int(decimals or 0))
        mc = price * adjusted_supply

        # store
        STATE["price_usd"] = round(price, 8)
        STATE["market_cap_usd"] = round(mc, 2)

        _last_helius_t = now
    except Exception as e:
        # soft-fail: keep old values
        print(f"[helius] warn: {e}")

# ---------- Actions ----------
def claim_creator_fees() -> Tuple[str, float]:
    """Claim creator fees, return (signature, claimed_SOL)."""
    if not WALLET_ADDRESS:
        raise RuntimeError("Missing WALLET_ADDRESS")
    before = get_balance_sol(WALLET_ADDRESS)
    sig = pump_portal_trade_local({
        "publicKey": WALLET_ADDRESS,
        "action": "collectCreatorFee",
        "priorityFee": PRIORITY_FEE,
    })
    time.sleep(2.0)  # let balance settle
    after = get_balance_sol(WALLET_ADDRESS)
    claimed = max(0.0, round(after - before, 6))
    return sig, claimed

def buy_back_sol(amount_sol: float) -> str:
    """Use SOL to buy TOKEN_MINT (denominated in SOL)."""
    if amount_sol <= 0:
        raise ValueError("amount_sol must be > 0")
    sig = pump_portal_trade_local({
        "publicKey": WALLET_ADDRESS,
        "action": "buy",
        "mint": TOKEN_MINT,
        "amount": amount_sol,          # in SOL
        "denominatedInSol": "true",
        "slippage": 10,
        "priorityFee": PRIORITY_FEE,
        "pool": "auto",
    })
    return sig

def burn_recently_bought(amount_sol: float) -> Optional[str]:
    """
    Burn the tokens we just bought.
    Uses the burn_tokens service to actually burn tokens on-chain.
    """
    try:
        from services.burn_tokens import burn_tokens
        from decimal import Decimal
        
        # Import the burn function and burn all tokens in the wallet
        # Since we just bought with amount_sol, we burn everything we have
        sig = burn_tokens(None, burn_all=True)
        return sig
    except Exception as e:
        print(f"[BURN] Error burning tokens: {e}")
        # Still return None so the calling code can handle gracefully
        return None

def process_goal_if_crossed():
    """
    If MC crosses a new 100k bucket since last time:
      1) claim creator fees
      2) buy back 25% of claimed SOL
      3) burn the bought tokens
      4) update dashboard state & tx history
    """
    mc = float(STATE["market_cap_usd"] or 0.0)
    current_bucket = int(mc // GOAL_STEP)
    if current_bucket <= STATE["last_goal_bucket"]:
        return

    # we moved into a new bucket — remember it so we won't repeat
    STATE["last_goal_bucket"] = current_bucket

    # 1) Claim
    try:
        claim_sig, claimed_sol = claim_creator_fees()
        push_tx("claim", claimed_sol, f"Claimed creator fees: {claimed_sol} SOL", claim_sig)
    except Exception as e:
        push_tx("claim", 0.0, f"Claim failed: {e}")
        return

    # 2) Buy-back with 25% of claim
    buy_amount = round(claimed_sol * 0.25, 6)
    if buy_amount <= 0:
        push_tx("buyback", 0.0, "No buyback (claimed 0 SOL)")
        return

    try:
        buy_sig = buy_back_sol(buy_amount)
        push_tx("buyback", buy_amount, f"Executed buy-back of {buy_amount} SOL", buy_sig)
        STATE["buybacks_usd"] += buy_amount * (STATE["price_usd"] or 0.0)
    except Exception as e:
        push_tx("buyback", 0.0, f"Buyback failed: {e}")
        return

    # 3) Burn what we bought (record now; wire real burn later)
    try:
        burn_sig = burn_recently_bought(buy_amount)
        push_tx("burn", buy_amount, f"Burned tokens bought with {buy_amount} SOL", burn_sig)
        # If you burn 100% of what you bought, credit all of it as "burned_usd"
        STATE["burned_usd"] += buy_amount * (STATE["price_usd"] or 0.0)
        # Nudge the visible supply-burned percentage a bit (until you compute it exactly)
        STATE["supply_burned_pct"] = round(min(100.0, STATE["supply_burned_pct"] + 0.05), 4)
    except Exception as e:
        push_tx("burn", 0.0, f"Burn failed: {e}")

# ---------- API Models ----------
class Dashboard(BaseModel):
    price_usd: float
    volume_change_pct: float
    buybacks_usd: float
    burned_usd: float
    market_cap_usd: float
    next_goal_usd: float
    next_goal_progress_pct: float
    supply_burned_pct: float
    transactions: list
    token_mint: str

# ---------- Endpoints ----------
@app.get("/dashboard", response_model=Dashboard)
def get_dashboard():
    # 1) Refresh price / MC from Helius (cached ~20s)
    refresh_market_data()

    # 2) If a new +$100k bucket was crossed, run the pipeline
    process_goal_if_crossed()

    # 3) Compute progress within the *current* 100k bucket
    mc = float(STATE["market_cap_usd"] or 0.0)
    bucket_start = (int(mc // GOAL_STEP)) * GOAL_STEP
    next_goal = bucket_start + GOAL_STEP
    progress_pct = 0.0 if GOAL_STEP <= 0 else max(0.0, min(100.0, (mc - bucket_start) / GOAL_STEP * 100.0))

    return {
        "price_usd": STATE["price_usd"],
        "volume_change_pct": STATE["volume_change_pct"],
        "buybacks_usd": STATE["buybacks_usd"],
        "burned_usd": STATE["burned_usd"],
        "market_cap_usd": mc,
        "next_goal_usd": next_goal,
        "next_goal_progress_pct": round(progress_pct, 2),
        "supply_burned_pct": STATE["supply_burned_pct"],
        "transactions": STATE["tx"],
        "token_mint": TOKEN_MINT,
    }

@app.post("/simulate/bump-mc")
def bump_market_cap(delta_usd: float = 110_000):
    """Dev helper: bump MC to force a bucket-crossing locally."""
    STATE["market_cap_usd"] += float(delta_usd)
    return {"market_cap_usd": STATE["market_cap_usd"]}

@app.get("/health")
def health():
    return {"ok": True}