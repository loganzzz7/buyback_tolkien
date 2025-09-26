import os
import argparse
from decimal import Decimal, ROUND_DOWN

from dotenv import load_dotenv
from solana.rpc.api import Client
from solana.publickey import PublicKey
from solana.keypair import Keypair
from solana.transaction import Transaction
from solana.rpc.commitment import Confirmed
from spl.token.constants import TOKEN_PROGRAM_ID as TOKEN_P1
from spl.token._layouts import ACCOUNT_LAYOUT
from spl.token.instructions import (
    get_associated_token_address,
    create_associated_token_account,
    burn_checked,
)
from base58 import b58decode

load_dotenv()

RPC_URL            = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com").strip()
WALLET_PRIVATE_KEY = os.getenv("WALLET_PRIVATE_KEY", "").strip()
TOKEN_MINT_STR     = os.getenv("TOKEN_MINT", "").strip()
# Optional override for Token-2022
TOKEN_PROGRAM_ID_STR = os.getenv("TOKEN_PROGRAM_ID", "").strip()  # e.g., "TokenzQd..."; default is classic Token Program

if not (RPC_URL and WALLET_PRIVATE_KEY and TOKEN_MINT_STR):
    raise SystemExit("Missing required .env: SOLANA_RPC_URL, WALLET_PRIVATE_KEY, TOKEN_MINT")

TOKEN_MINT = PublicKey(TOKEN_MINT_STR)
TOKEN_PROGRAM_ID = PublicKey(TOKEN_PROGRAM_ID_STR) if TOKEN_PROGRAM_ID_STR else TOKEN_P1

def load_keypair_from_base58(b58: str) -> Keypair:
    """Accept either 64-byte secret key (base58) or JSON array; here we expect base58 PK."""
    try:
        raw = b58decode(b58)
        return Keypair.from_secret_key(raw)
    except Exception as e:
        raise SystemExit(f"Invalid WALLET_PRIVATE_KEY base58: {e}")

def get_mint_decimals(client: Client, mint: PublicKey) -> int:
    mi = client.get_token_supply(mint, commitment=Confirmed)
    if not mi.get("result") or not mi["result"].get("value"):
        raise RuntimeError(f"Cannot fetch mint supply/decimals for {mint}")
    return int(mi["result"]["value"]["decimals"])

def read_token_balance_raw(client: Client, ata: PublicKey) -> int:
    """Return raw token units (integer, before decimals)."""
    ai = client.get_account_info(ata, commitment=Confirmed)
    if not ai.get("result") or not ai["result"].get("value"):
        return 0
    data = ai["result"]["value"]["data"][0]
    import base64
    acc = ACCOUNT_LAYOUT.parse(base64.b64decode(data))
    return int(acc.amount)

def ensure_ata(client: Client, owner: PublicKey, mint: PublicKey, payer: Keypair) -> PublicKey:
    ata = get_associated_token_address(owner, mint, program_id=TOKEN_PROGRAM_ID)
    info = client.get_account_info(ata, commitment=Confirmed)
    if not info.get("result") or not info["result"].get("value"):
        # Need to create ATA (owner must sign)
        tx = Transaction(fee_payer=payer.public_key)
        tx.add(
            create_associated_token_account(
                payer=payer.public_key,
                owner=owner,
                mint=mint,
                program_id=TOKEN_PROGRAM_ID,
            )
        )
        sig = client.send_transaction(tx, payer, opts={"skip_preflight": False})
        client.confirm_transaction(sig["result"], commitment=Confirmed)
    return ata

def burn_tokens(amount_tokens: Decimal | None, burn_all: bool = False) -> str:
    client = Client(RPC_URL, commitment=Confirmed)
    payer = load_keypair_from_base58(WALLET_PRIVATE_KEY)
    owner = payer.public_key

    decimals = get_mint_decimals(client, TOKEN_MINT)
    factor = Decimal(10) ** decimals

    ata = ensure_ata(client, owner, TOKEN_MINT, payer)
    raw_bal = Decimal(read_token_balance_raw(client, ata))

    if raw_bal <= 0:
        raise SystemExit("Nothing to burn: token balance is 0")

    if burn_all:
        raw_to_burn = int(raw_bal)
    else:
        if amount_tokens is None:
            raise SystemExit("--amount is required unless --all is set")
        # Convert tokens → raw
        raw_to_burn = int((amount_tokens * factor).to_integral_value(rounding=ROUND_DOWN))
        if raw_to_burn <= 0:
            raise SystemExit("Amount after decimals rounds to zero")
        if raw_to_burn > raw_bal:
            raise SystemExit("Not enough balance to burn the requested amount")

    # Build and send transaction
    tx = Transaction(fee_payer=owner)
    tx.add(
        burn_checked(
            program_id=TOKEN_PROGRAM_ID,
            account=ata,
            mint=TOKEN_MINT,
            owner=owner,
            amount=raw_to_burn,
            decimals=decimals,
            multi_signers=[],
        )
    )
    sig = client.send_transaction(tx, payer, opts={"skip_preflight": False})
    client.confirm_transaction(sig["result"], commitment=Confirmed)
    return sig["result"]

def main():
    parser = argparse.ArgumentParser(description="Burn SPL tokens you own.")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="Burn your full token balance")
    g.add_argument("--amount", type=str, help="Amount of tokens (human units) to burn, e.g., 123.45")
    args = parser.parse_args()

    if args.all:
        sig = burn_tokens(None, burn_all=True)
    else:
        amt = Decimal(args.amount)
        sig = burn_tokens(amt, burn_all=False)

    print(f"Burn signature: https://solscan.io/tx/{sig}")

if __name__ == "__main__":
    main()