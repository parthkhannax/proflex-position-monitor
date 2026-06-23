# Proflex Position Monitor

Live breakeven monitoring dashboard for Proflex Growth Gazette & Income Insider open options positions. Fetches real-time prices via yfinance, benchmarks against breakevens, and fires Telegram + email alerts when positions are threatened.

**Live dashboard → [parthkhannax.github.io/proflex-position-monitor](https://parthkhannax.github.io/proflex-position-monitor/)**

---

## What It Does

- Fetches live stock prices and option chain data (bid/ask/IV) via yfinance
- Computes breakeven status for each open position using strategy-specific logic
- Fires 3-tier alerts (Warning → Critical → Breached) via Telegram and email
- Writes `data/status.json` and pushes to GitHub so the dashboard always reflects the latest run
- 60-minute alert cooldown prevents notification spam

---

## Alert Tiers

| Level | Trigger | Action |
|---|---|---|
| ✅ OK | Comfortable distance from breakeven | No alert |
| ⚠️ WARNING | Within 2% of breakeven / strike | Alert fires |
| 🔴 CRITICAL | At or past breakeven | Alert fires |
| 🚨 BREACHED | >5% past breakeven / deep ITM | Alert fires |

### Breakeven Logic Per Strategy

**Short Put**
- Breakeven = Strike − Premium Collected
- Risk direction: downside (stock falls)
- WARNING: stock within 2% above breakeven
- CRITICAL: stock at or below breakeven
- BREACHED: stock >5% below breakeven

**Covered Call**
- Threat: stock approaching / exceeding the call strike (assignment risk)
- Risk direction: upside (stock rises)
- WARNING: stock within 2% below strike
- CRITICAL: stock at or above strike (call ITM)
- BREACHED: stock >5% above strike (deep ITM)

**Bull Put Spread**
- Breakeven = Higher Strike − Net Premium Collected
- Max loss floor = Lower Strike
- Risk direction: downside (stock falls)
- WARNING: stock within 2% above breakeven
- CRITICAL: stock below breakeven, above lower strike
- BREACHED: stock at or below lower strike (max loss zone)

---

## Project Structure

```
proflex-position-monitor/
├── index.html          # GitHub Pages dashboard (auto-refreshes every 60s)
├── monitor.py          # Main monitoring script — run this locally or via cron
├── positions.json      # Your open positions (edit this to add/remove trades)
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
├── .env                # Your secrets — NEVER committed (in .gitignore)
├── data/
│   └── status.json     # Output written by monitor.py, read by the dashboard
└── alert_history.json  # Cooldown tracker — auto-managed, not committed
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/parthkhannax/proflex-position-monitor.git
cd proflex-position-monitor
pip install -r requirements.txt
```

### 2. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here       # From @BotFather on Telegram
TELEGRAM_CHAT_ID=your_chat_id_here           # Send /start to your bot first
ALERT_EMAIL=parth@proflexfinance.com         # Alert destination
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_gmail@gmail.com               # Sending Gmail account
SMTP_PASS=your_16char_app_password           # Gmail App Password (not your login password)
GITHUB_REPO=parthkhannax/proflex-position-monitor
```

**Gmail App Password:** Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords) → Create → copy the 16-character code (no spaces).

**Telegram:** Create a bot via [@BotFather](https://t.me/BotFather), get the token, then send `/start` to your bot so it can find your chat ID.

### 3. Add your open positions

Edit `positions.json`. Each position follows this structure:

**Short Put:**
```json
{
  "id": "nvda-short-put-jul17",
  "ticker": "NVDA",
  "strategy": "Short Put",
  "strike": 200,
  "expiry": "2026-07-17",
  "premium_collected": 6.30,
  "contracts": 1,
  "open_date": "2026-06-05",
  "newsletter": "Growth Gazette",
  "rating": "A",
  "notes": "Sell $200 Put Jul 17"
}
```

**Covered Call:**
```json
{
  "id": "aapl-covered-call-aug21",
  "ticker": "AAPL",
  "strategy": "Covered Call",
  "strike": 305,
  "expiry": "2026-08-21",
  "premium_collected": 5.70,
  "contracts": 1,
  "open_date": "2026-05-21",
  "newsletter": "Growth Gazette",
  "rating": "B",
  "notes": "Sell $305 Call Aug 21"
}
```

**Bull Put Spread:**
```json
{
  "id": "tsla-bull-put-aug21",
  "ticker": "TSLA",
  "strategy": "Bull Put Spread",
  "strike_high": 300,
  "strike_low": 270,
  "expiry": "2026-08-21",
  "premium_collected": 4.20,
  "contracts": 1,
  "open_date": "2026-06-01",
  "newsletter": "Growth Gazette",
  "rating": "B",
  "notes": ""
}
```

Supported strategy values: `Short Put`, `Covered Call`, `Bull Put Spread`

### 4. Run manually

```bash
python3 monitor.py
```

This will:
1. Fetch live prices and option chain data for every position
2. Compute breakeven status and alert tier
3. Fire Telegram + email alerts for any WARNING / CRITICAL / BREACHED positions
4. Write `data/status.json`
5. Commit and push to GitHub so the live dashboard updates

### 5. Set up cron (automatic every 15 min, market hours)

```bash
crontab -e
```

Add this line:

```
*/15 14-21 * * 1-5 cd /Users/parthkhanna/Desktop/claude-work/proflex-monitor && /usr/bin/python3 monitor.py >> monitor.log 2>&1
```

`14–21 UTC` = 9:30am–4:30pm ET (US market hours), Monday–Friday.

To check the log:
```bash
tail -f /Users/parthkhanna/Desktop/claude-work/proflex-monitor/monitor.log
```

---

## Managing Positions

### Add a new position
Edit `positions.json` — add a new object to the `positions` array using the templates above. Use a unique `id` (e.g. `msft-short-put-jul17`).

### Close / remove a position
Delete its entry from `positions.json`. The dashboard and monitor will stop tracking it on the next run.

### After any change to positions.json
```bash
git add positions.json
git commit -m "positions: add/remove TICKER"
git push
```

---

## Dashboard

The GitHub Pages dashboard at `https://parthkhannax.github.io/proflex-position-monitor/` shows:

- Summary bar: total positions, safe count, warning count, critical/breached count
- Full positions table: ticker, strategy, strike, expiry, premium collected, breakeven, live price, distance %, status badge
- Option chain data inline: last price, bid/ask, implied volatility
- Auto-refreshes every 60 seconds
- Hover any row to see the full alert message

The dashboard reads `data/status.json` which `monitor.py` pushes to GitHub on every run.

---

## Troubleshooting

**Telegram "chat not found"**
Send `/start` to your bot in Telegram before the first run. The bot must receive at least one message from you to know your chat ID exists.

**Email not sending**
Use a Gmail App Password (not your account password). Must have 2FA enabled on the Gmail account. Generate at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords).

**Price fetch failed**
yfinance occasionally rate-limits. The position will show `ERROR` status for that run and retry next cycle. No alert fires on a price fetch error.

**Option data missing**
yfinance may not have data for very far-out expiries or illiquid strikes. The monitor falls back to the nearest available strike. Stock price monitoring and alerts still work regardless.

**Alert not firing (on cooldown)**
Each alert level per position has a 60-minute cooldown. To reset and force a re-fire:
```bash
rm alert_history.json
python3 monitor.py
```

**Dashboard not updating**
Check that `monitor.py` is running and that the git push step completed (look for `✓ Pushed to GitHub` in the output). The dashboard reads `data/status.json` from the `main` branch.
