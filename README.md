# NeonTrade — Automated Live Opportunity Scanner

This build adds a real MT5 candle-based opportunity scanner for **BTCUSD, XAUUSD, EURUSD and GBPUSD**.

## What it calculates
- EMA 20 and EMA 50
- RSI (14)
- MACD and signal line
- ATR (14)
- Local swing support/resistance
- Current bid/ask and spread
- BUY / SELL / NEUTRAL signal
- 0–100 confidence score
- Suggested entry, stop-loss and take-profit

The scanner uses **M15 candles from the connected MT5 terminal** and refreshes the dashboard every 30 seconds. Broker symbol suffixes/prefixes are resolved automatically where possible (for example `XAUUSD.a`).

## Run
```bash
cd backend
python -m venv .venv
.venv\\Scripts\\activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open `frontend/index.html` in the browser and connect MT5 through the broker connection flow from the previous build, or set `MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER` and optional `MT5_PATH` as server environment variables.

## Important
Signals are analytical outputs, not guarantees. The scanner is not a promise of profitability. Keep live order execution disabled while validating the strategy with MT5 Strategy Tester and a demo account. Before production use, add authentication, HTTPS, server-side risk limits, persistent audit logs, and a kill switch.
