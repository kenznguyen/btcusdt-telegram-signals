# BTCUSDT M15/H1 Telegram Signal Bot (signal-only)

This project reads public Binance USDⓈ-M Futures candles and sends Telegram messages. It does **not** place orders and does not use Binance API keys.

## Included logic

- M15 RSI(14), DMI(14), ADX smoothing 14
- H1 direction using the last completed H1 candle, ADX > 20
- LONG: H1 bullish and M15 RSI crosses above 30
- SHORT: H1 bearish and M15 RSI crosses below 70
- One DCA maximum: ROE <= -20%, M15 ADX > 45, plus the RSI conditions from the supplied Pine script
- TP: LONG RSI >= 76 / SHORT RSI <= 24 and ROE >= 5%
- Soft SL: -35% ROE threshold, H1 reversal ADX > 35, plus two candle confirmation
- Emergency SL: -90% ROE threshold
- Virtual position and average entry are stored in `state.json`

## Important limitations

1. This is a signal-only simulation, not a live trading or execution engine.
2. Python indicator values can differ slightly from TradingView because indicator seeding, candle feeds, and evaluation timing may differ. Compare signals against TradingView before sharing them.
3. A candle's high/low shows that a threshold was touched but cannot reveal intrabar event order or actual fill price. The bot uses a conservative exit priority: Emergency SL, Soft SL, then TP.
4. The script processes closed candles only. GitHub scheduled Actions can be delayed, so this is not a guaranteed real-time/24-7 alert service.
5. On first run, it begins from the newest closed candle and starts with a flat virtual position. It cannot recover an open position that existed before deployment.
6. The bot uses fixed margin inputs for the simulation. It does not model liquidation, funding, slippage, maintenance margin, or all fees.
7. It currently reports DCA and exits as signals; users must independently verify and act on them.

## Create the Telegram bot

1. In Telegram, open `@BotFather`, send `/newbot`, and follow the steps.
2. Save the bot token privately.
3. Add the bot to your Telegram group. If the group is private, ensure the bot can post messages.
4. Obtain the chat ID. One simple way: send a message in the group, then open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and read `message.chat.id`. Never share the token or the returned URL. If getUpdates is empty, send another group message after adding the bot.
5. Test privately first. Revoke and regenerate the token with BotFather if it is exposed.

## Deploy with GitHub Actions (free to start)

1. Create a GitHub repository. A public repository is easiest for staying within free Actions allowances, but remember the source code will be public. Never put tokens or other secrets in code.
2. Upload `bot.py`, `requirements.txt`, `state.json`, and `.github/workflows/signals.yml`.
3. In repository Settings → Secrets and variables → Actions → New repository secret, add:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
4. In Settings → Actions → General, ensure workflow permissions allow read/write repository contents. The workflow also requests `contents: write`.
5. In Actions, run `BTCUSDT Telegram Signals` manually once using **Run workflow**.
6. Check the run log. If successful, verify the Telegram message (if a new signal exists, it may send none).
7. Wait for scheduled runs and check Actions history.

### Free-tier caveats

GitHub Actions scheduled runs are not exact-time guarantees and can be delayed. Repositories with no activity for 60 days may have scheduled workflows disabled. Free usage quotas apply. If this needs dependable 24/7 delivery, a continuously running host may cost money.

## Test before inviting followers

- Compare at least 20 historical or forward signals against the Pine Strategy.
- Confirm H1 values are from a fully closed H1 candle.
- Check the LONG/SHORT entry, DCA, TP, Soft SL, and Emergency SL messages.
- Watch for missed/delayed runs, duplicate messages, and state persistence.
- Do not present backtest or paper results as guaranteed future performance.

## Run locally

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="your-token"
export TELEGRAM_CHAT_ID="your-chat-id"
python bot.py
```

On Windows PowerShell, set environment variables with `$env:TELEGRAM_BOT_TOKEN="..."` and `$env:TELEGRAM_CHAT_ID="..."`.

The bot is intentionally not connected to any order endpoint. Do not add trading permissions or API secrets.
