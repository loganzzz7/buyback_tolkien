import requests
from solders.transaction import VersionedTransaction
from solders.keypair import Keypair
from solders.commitment_config import CommitmentLevel
from solders.rpc.requests import SendVersionedTransaction
from solders.rpc.config import RpcSendTransactionConfig

response = requests.post(url="https://pumpportal.fun/api/trade-local", data={
    "publicKey": "Your public key here",
    "action": "collectCreatorFee",
    "priorityFee": 0.000001,
})

keypair = Keypair.from_base58_string("Your base 58 private key here")
tx = VersionedTransaction(VersionedTransaction.from_bytes(response.content).message, [keypair])

commitment = CommitmentLevel.Confirmed
config = RpcSendTransactionConfig(preflight_commitment=commitment)
txPayload = SendVersionedTransaction(tx, config)

response = requests.post(
    url="Your RPC Endpoint here - Eg: https://api.mainnet-beta.solana.com/",
    headers={"Content-Type": "application/json"},
    data=SendVersionedTransaction(tx, config).to_json()
)
txSignature = response.json()['result']
print(f'Transaction: https://solscan.io/tx/{txSignature}')
