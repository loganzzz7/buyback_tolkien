# Tolkien Buyback Dashboard

A production-ready crypto dashboard that automatically executes buybacks and burns when market cap milestones are reached.

## Features

- **Automatic Goal Processing**: Triggers buyback/burn cycle every $100k market cap increase
- **Real-time Price Data**: Fetches live data from Helius API
- **Transaction History**: Displays recent claims, buybacks, and burns with Solscan links
- **Progress Tracking**: Visual progress bars for next goal and supply burned
- **Production Ready**: Fully configured for deployment with proper error handling

## Quick Setup

1. **Clone and Setup**:
   ```bash
   git clone <your-repo>
   cd buyback_tolkien
   ```

2. **Configure Environment**:
   ```bash
   cp env.template .env
   # Edit .env with your actual values
   ```

3. **Start Backend**:
   ```bash
   ./start.sh
   ```

4. **Deploy Frontend**:
   - Upload `frontend/` folder to your web hosting
   - Set `window.API_BASE` in production to your backend URL

## Environment Variables

Required in your `.env` file:

- `WALLET_ADDRESS` - Your wallet's public key
- `WALLET_PRIVATE_KEY` - Your wallet's private key (base58 format)
- `TOKEN_MINT` - Your token's mint address
- `HELIUS_API_KEY` - Helius API key for price data
- `FRONTEND_ORIGIN` - Your frontend domain for CORS

Optional:
- `SOLANA_RPC_URL` - Solana RPC endpoint (defaults to mainnet)
- `PRIORITY_FEE` - Transaction priority fee (defaults to 0.000001)
- `TOKEN_PROGRAM_ID` - For Token-2022 tokens (leave empty for standard SPL)

## How It Works

1. **Price Monitoring**: Every 5 seconds, fetches current price and market cap
2. **Goal Detection**: Checks if market cap crossed a new $100k milestone
3. **Automated Execution**:
   - Claims creator fees from PumpPortal
   - Buys back tokens with 25% of claimed SOL
   - Burns all purchased tokens
4. **UI Updates**: Real-time dashboard updates with transaction history

## Production Deployment

### Backend (Railway/Render/Fly.io)
```bash
# Install dependencies
pip install -r backend/requirements.txt

# Start server
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

### Frontend (Vercel/Netlify)
```javascript
// Set this in your HTML before loading app.js
window.API_BASE = "https://your-backend-domain.com";
```

## API Endpoints

- `GET /dashboard` - Returns all dashboard data
- `GET /health` - Health check
- `POST /simulate/bump-mc` - Dev helper to test goal crossing

## Security Notes

- Keep your `WALLET_PRIVATE_KEY` secure and never commit it to version control
- Use environment variables in production
- Consider using a dedicated wallet for this service
- Monitor transaction fees and slippage settings

## Troubleshooting

- **"Missing critical .env values"**: Ensure all required environment variables are set
- **Helius API errors**: Check your API key and rate limits
- **Transaction failures**: Verify wallet has sufficient SOL for fees
- **CORS errors**: Ensure `FRONTEND_ORIGIN` matches your frontend domain exactly
