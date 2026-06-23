#!/usr/bin/env python3
"""
Proflex Position Monitor
Fetches live prices, computes breakeven status, fires alerts, pushes status.json to GitHub.
Run: python3 monitor.py
Cron (every 15 min market hours): */15 9-16 * * 1-5 cd ~/Desktop/claude-work/proflex-monitor && python3 monitor.py
"""

import json
import os
import smtplib
import subprocess
import time
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", "parth@proflexfinance.com")
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
GITHUB_REPO = os.getenv("GITHUB_REPO", "")

BASE_DIR = Path(__file__).parent
POSITIONS_FILE = BASE_DIR / "positions.json"
STATUS_FILE = BASE_DIR / "data" / "status.json"
ALERT_HISTORY_FILE = BASE_DIR / "alert_history.json"

# Alert cooldown: don't re-fire the same alert within X minutes
ALERT_COOLDOWN_MINUTES = 60


# ── Breakeven Framework ──────────────────────────────────────────────────────

def compute_status(pos: dict, price: float) -> dict:
    """
    Returns breakeven, distance_pct, alert_level, and a human message.

    Alert levels:
      OK        — comfortable, no action needed
      WARNING   — within 2% of breakeven / strike threat
      CRITICAL  — at or past breakeven (or stock at/above call strike)
      BREACHED  — significantly past breakeven (>5% for puts, >5% above call strike)
    """
    strategy = pos["strategy"]
    expiry = pos.get("expiry", "")

    if strategy == "Short Put":
        # Breakeven = Strike - Premium Collected
        # Risk: stock falls below breakeven
        breakeven = pos["strike"] - pos["premium_collected"]
        distance_pct = (price - breakeven) / breakeven * 100  # positive = safe

        if distance_pct > 2:
            level = "OK"
            msg = f"Stock ${price:.2f} is {distance_pct:.1f}% above breakeven ${breakeven:.2f}. Safe."
        elif distance_pct > 0:
            level = "WARNING"
            msg = f"⚠️ Stock ${price:.2f} is within {distance_pct:.1f}% of breakeven ${breakeven:.2f}. Monitor closely."
        elif distance_pct > -5:
            level = "CRITICAL"
            msg = f"🔴 CRITICAL: Stock ${price:.2f} has breached breakeven ${breakeven:.2f} by {abs(distance_pct):.1f}%. Consider rolling or closing."
        else:
            level = "BREACHED"
            msg = f"🚨 BREACHED: Stock ${price:.2f} is {abs(distance_pct):.1f}% below breakeven ${breakeven:.2f}. Immediate review required."

        return {"breakeven": breakeven, "breakeven_label": f"${breakeven:.2f}", "distance_pct": distance_pct,
                "alert_level": level, "message": msg, "risk_direction": "downside"}

    elif strategy == "Covered Call":
        # Threat: stock approaches / exceeds the strike (shares get called away)
        # Breakeven on position = cost_basis - premium_collected (not tracked here — monitor strike proximity)
        strike = pos["strike"]
        distance_pct = (strike - price) / strike * 100  # positive = stock below strike (safe)

        if distance_pct > 2:
            level = "OK"
            msg = f"Stock ${price:.2f} is {distance_pct:.1f}% below strike ${strike:.2f}. Call not threatened."
        elif distance_pct > 0:
            level = "WARNING"
            msg = f"⚠️ Stock ${price:.2f} is within {distance_pct:.1f}% of call strike ${strike:.2f}. Risk of assignment approaching."
        elif distance_pct > -5:
            level = "CRITICAL"
            msg = f"🔴 CRITICAL: Stock ${price:.2f} has exceeded call strike ${strike:.2f}. Call is ITM — assignment risk. Consider rolling up/out."
        else:
            level = "BREACHED"
            msg = f"🚨 BREACHED: Stock ${price:.2f} is {abs(distance_pct):.1f}% above call strike ${strike:.2f}. Deep ITM — shares likely called away at expiry."

        return {"breakeven": strike, "breakeven_label": f"${strike:.2f} (call strike)",
                "distance_pct": distance_pct, "alert_level": level, "message": msg, "risk_direction": "upside"}

    elif strategy == "Bull Put Spread":
        # Breakeven = Higher Strike - Net Premium Collected
        # Max loss zone: stock <= lower strike
        # Risk: stock falls below breakeven
        strike_high = pos["strike_high"]
        strike_low = pos["strike_low"]
        breakeven = strike_high - pos["premium_collected"]
        distance_pct = (price - breakeven) / breakeven * 100  # positive = safe

        if distance_pct > 2:
            level = "OK"
            msg = f"Stock ${price:.2f} is {distance_pct:.1f}% above breakeven ${breakeven:.2f}. Spread intact."
        elif distance_pct > 0:
            level = "WARNING"
            msg = f"⚠️ Stock ${price:.2f} is within {distance_pct:.1f}% of breakeven ${breakeven:.2f}. Spread under pressure."
        elif price > strike_low:
            level = "CRITICAL"
            msg = f"🔴 CRITICAL: Stock ${price:.2f} below breakeven ${breakeven:.2f}. Still above max-loss floor ${strike_low:.2f} — consider closing to limit loss."
        else:
            level = "BREACHED"
            msg = f"🚨 BREACHED: Stock ${price:.2f} below lower strike ${strike_low:.2f}. Max loss zone reached. Close immediately."

        return {"breakeven": breakeven, "breakeven_label": f"${breakeven:.2f} (spread breakeven)",
                "distance_pct": distance_pct, "alert_level": level, "message": msg,
                "risk_direction": "downside", "max_loss_floor": strike_low}

    else:
        return {"breakeven": None, "breakeven_label": "N/A", "distance_pct": None,
                "alert_level": "UNKNOWN", "message": f"Unknown strategy: {strategy}", "risk_direction": "unknown"}


# ── Price + Option Chain Fetch ───────────────────────────────────────────────

def fetch_price(ticker: str, retries: int = 3) -> float | None:
    for attempt in range(1, retries + 1):
        try:
            t = yf.Ticker(ticker)
            info = t.fast_info
            price = float(info.last_price)
            if price and price > 0:
                return round(price, 2)
            raise ValueError(f"invalid price: {price}")
        except Exception as e:
            print(f"  [price attempt {attempt}/{retries}] {ticker}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


def fetch_option_data(ticker: str, expiry: str, strike: float, option_type: str, retries: int = 3) -> dict | None:
    """
    Fetch live bid/ask/last for a specific option contract from yfinance.
    option_type: 'put' or 'call'
    expiry: 'YYYY-MM-DD'
    Returns dict with bid, ask, last, iv, volume or None on failure.
    """
    for attempt in range(1, retries + 1):
        try:
            t = yf.Ticker(ticker)
            available = t.options  # list of expiry date strings

            # Find closest matching expiry
            target = date.fromisoformat(expiry)
            closest = min(available, key=lambda d: abs(date.fromisoformat(d) - target)) if available else None
            if not closest:
                return None

            chain = t.option_chain(closest)
            df = chain.puts if option_type == "put" else chain.calls
            row = df[df["strike"] == strike]
            if row.empty:
                df["dist"] = (df["strike"] - strike).abs()
                row = df.nsmallest(1, "dist")

            if row.empty:
                return None

            r = row.iloc[0]
            return {
                "bid": round(float(r.get("bid", 0)), 2),
                "ask": round(float(r.get("ask", 0)), 2),
                "last": round(float(r.get("lastPrice", 0)), 2),
                "iv": round(float(r.get("impliedVolatility", 0)) * 100, 1),
                "volume": int(r.get("volume", 0) or 0),
                "expiry_used": closest,
            }
        except Exception as e:
            print(f"  [option attempt {attempt}/{retries}] {ticker} {expiry} {strike}: {e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    return None


# ── Alert History (cooldown) ─────────────────────────────────────────────────

def load_alert_history() -> dict:
    if ALERT_HISTORY_FILE.exists():
        return json.loads(ALERT_HISTORY_FILE.read_text())
    return {}


def save_alert_history(history: dict):
    ALERT_HISTORY_FILE.write_text(json.dumps(history, indent=2))


def should_alert(pos_id: str, level: str, history: dict) -> bool:
    key = f"{pos_id}:{level}"
    last = history.get(key)
    if not last:
        return True
    elapsed = (datetime.utcnow() - datetime.fromisoformat(last)).total_seconds() / 60
    return elapsed >= ALERT_COOLDOWN_MINUTES


def record_alert(pos_id: str, level: str, history: dict):
    key = f"{pos_id}:{level}"
    history[key] = datetime.utcnow().isoformat()


# ── Notification Senders ─────────────────────────────────────────────────────

def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("  [telegram] not configured — skipping")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
        if r.ok:
            print("  [telegram] sent ✓")
        else:
            print(f"  [telegram] failed: {r.text}")
    except Exception as e:
        print(f"  [telegram] error: {e}")


def send_email(subject: str, body_html: str):
    if not SMTP_USER or not SMTP_PASS:
        print("  [email] SMTP not configured — skipping")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_EMAIL
        msg.attach(MIMEText(body_html, "html"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, ALERT_EMAIL, msg.as_string())
        print("  [email] sent ✓")
    except Exception as e:
        print(f"  [email] error: {e}")


LEVEL_EMOJI = {"WARNING": "⚠️", "CRITICAL": "🔴", "BREACHED": "🚨"}
LEVEL_COLOR = {"OK": "#22c55e", "WARNING": "#f59e0b", "CRITICAL": "#ef4444", "BREACHED": "#7f1d1d"}


def fire_alerts(pos: dict, status: dict, price: float):
    level = status["alert_level"]
    if level == "OK":
        return

    ticker = pos["ticker"]
    strategy = pos["strategy"]
    emoji = LEVEL_EMOJI.get(level, "⚠️")
    breakeven_label = status["breakeven_label"]
    dist = status["distance_pct"]
    dist_str = f"{abs(dist):.1f}%" if dist is not None else "N/A"
    msg = status["message"]
    expiry = pos.get("expiry", "")
    strike = pos.get("strike") or pos.get("strike_high", "")

    tg_text = (
        f"{emoji} <b>Proflex Position Alert</b>\n\n"
        f"<b>{ticker} – {strategy}</b>\n"
        f"Strike: ${strike} | Expiry: {expiry}\n"
        f"Current Price: <b>${price:.2f}</b>\n"
        f"Breakeven: <b>{breakeven_label}</b>\n"
        f"Distance: <b>{dist_str}</b>\n\n"
        f"{msg}\n\n"
        f"<i>Newsletter: {pos.get('newsletter', '')} | Rating: {pos.get('rating', '')}</i>"
    )

    email_subject = f"[{level}] Proflex Alert: {ticker} {strategy} – Breakeven Threatened"
    color = LEVEL_COLOR.get(level, "#ef4444")
    email_body = f"""
    <div style="font-family:Inter,sans-serif;max-width:600px;margin:0 auto;background:#f5f5f0;padding:24px;border-radius:8px;">
      <div style="background:#141c25;padding:16px 24px;border-radius:6px 6px 0 0;">
        <span style="color:#c9a961;font-weight:700;font-size:18px;">PROFLEX</span>
        <span style="color:#f5f5f0;font-size:14px;margin-left:8px;">Position Monitor</span>
      </div>
      <div style="background:#fff;padding:24px;border-radius:0 0 6px 6px;border-top:4px solid {color};">
        <h2 style="margin:0 0 8px;color:{color};">{emoji} {level}: {ticker} – {strategy}</h2>
        <table style="width:100%;border-collapse:collapse;margin:16px 0;">
          <tr><td style="padding:6px 0;color:#666;">Ticker</td><td style="font-weight:600;">{ticker}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Strategy</td><td>{strategy}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Strike</td><td>${strike}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Expiry</td><td>{expiry}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Current Price</td><td style="font-weight:700;font-size:18px;">${price:.2f}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Breakeven</td><td style="font-weight:700;">{breakeven_label}</td></tr>
          <tr><td style="padding:6px 0;color:#666;">Distance</td><td style="color:{color};font-weight:700;">{dist_str}</td></tr>
        </table>
        <p style="background:#fef3c7;border-left:4px solid {color};padding:12px 16px;border-radius:4px;margin:16px 0;">{msg}</p>
        <p style="color:#999;font-size:12px;margin-top:24px;">Newsletter: {pos.get('newsletter','')} | Rating: {pos.get('rating','')} | {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</p>
      </div>
    </div>
    """

    send_telegram(tg_text)
    send_email(email_subject, email_body)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*50}")
    print(f"Proflex Monitor — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*50}")

    positions = json.loads(POSITIONS_FILE.read_text())["positions"]
    alert_history = load_alert_history()
    results = []

    for pos in positions:
        ticker = pos["ticker"]
        strategy = pos["strategy"]
        print(f"\n→ {ticker} | {strategy}")

        price = fetch_price(ticker)
        if price is None:
            print(f"  Could not fetch price — skipping")
            results.append({**pos, "current_price": None, "status": {"alert_level": "ERROR", "message": "Price fetch failed"}})
            continue

        print(f"  Price: ${price:.2f}")

        # Fetch option chain data
        expiry = pos.get("expiry", "")
        strike = pos.get("strike") or pos.get("strike_high")
        opt_type = "put" if "Put" in strategy else "call"
        option_data = fetch_option_data(ticker, expiry, strike, opt_type) if expiry and strike else None
        if option_data:
            print(f"  Option last: ${option_data['last']} | bid: ${option_data['bid']} | ask: ${option_data['ask']} | IV: {option_data['iv']}%")

        status = compute_status(pos, price)
        print(f"  Status: {status['alert_level']} | {status['message']}")

        # Fire alerts with cooldown
        level = status["alert_level"]
        if level in ("WARNING", "CRITICAL", "BREACHED"):
            if should_alert(pos["id"], level, alert_history):
                print(f"  → Firing {level} alert...")
                fire_alerts(pos, status, price)
                record_alert(pos["id"], level, alert_history)
            else:
                print(f"  → {level} alert on cooldown (< {ALERT_COOLDOWN_MINUTES}min since last)")

        results.append({
            **pos,
            "current_price": price,
            "option_data": option_data,
            "status": status,
        })

    save_alert_history(alert_history)

    # Write status.json for GitHub Pages frontend
    STATUS_FILE.parent.mkdir(exist_ok=True)
    output = {
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "positions": results,
    }
    STATUS_FILE.write_text(json.dumps(output, indent=2))
    print(f"\n✓ status.json written")

    # In GitHub Actions, the workflow commits + pushes status.json itself.
    if os.getenv("GITHUB_ACTIONS") == "true":
        print("✓ Running in GitHub Actions — workflow will commit status.json")
        return

    # Push to GitHub so Pages dashboard updates (local runs only)
    try:
        subprocess.run(["git", "add", "data/status.json"], cwd=BASE_DIR, check=True)
        commit_result = subprocess.run(
            ["git", "commit", "-m", f"monitor: update status {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"],
            cwd=BASE_DIR, capture_output=True, text=True
        )
        if commit_result.returncode == 0:
            push_result = subprocess.run(
                ["git", "push"], cwd=BASE_DIR, capture_output=True, text=True
            )
            if push_result.returncode == 0:
                print("✓ Pushed to GitHub")
            else:
                print(f"  [git push ERROR] {push_result.stderr.strip()}")
        elif "nothing to commit" in commit_result.stdout + commit_result.stderr:
            print("✓ No changes to push")
        else:
            print(f"  [git commit ERROR] {commit_result.stderr.strip()}")
    except Exception as e:
        print(f"  [git ERROR] {e}")

    print("\nDone.\n")


if __name__ == "__main__":
    main()
