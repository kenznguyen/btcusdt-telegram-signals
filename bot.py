import os
import json
import time
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd

# Signal-only bot. No Binance trading API keys or order endpoints are used.
SYMBOL = "BTCUSDT"
INTERVAL = "15m"
H1_INTERVAL = "1h"
LEVERAGE = float(os.getenv("LEVERAGE", "13"))
RSI_LEN = int(os.getenv("RSI_LENGTH", "14"))
DMI_LEN = int(os.getenv("DMI_LENGTH", "14"))
ADX_SMOOTH = int(os.getenv("ADX_SMOOTHING", "14"))
H1_ADX_ENTRY = float(os.getenv("H1_ADX_ENTRY", "20"))
LONG_RSI_ENTRY = float(os.getenv("LONG_RSI_ENTRY", "30"))
SHORT_RSI_ENTRY = float(os.getenv("SHORT_RSI_ENTRY", "70"))
LONG_RSI_TP = float(os.getenv("LONG_RSI_TP", "76"))
SHORT_RSI_TP = float(os.getenv("SHORT_RSI_TP", "24"))
TP_ROE = float(os.getenv("TP_ROE", "5"))
DCA_ROE = float(os.getenv("DCA_ROE", "-20"))
DCA_ADX = float(os.getenv("DCA_ADX", "45"))
SHORT_DCA_RSI = float(os.getenv("SHORT_DCA_RSI", "80"))
LONG_DCA_RSI = float(os.getenv("LONG_DCA_RSI", "20"))
SOFT_SL_ROE = float(os.getenv("SOFT_SL_ROE", "-35"))
EMERGENCY_SL_ROE = float(os.getenv("EMERGENCY_SL_ROE", "-90"))
SOFT_SL_ADX = float(os.getenv("SOFT_SL_ADX", "35"))
INITIAL_MARGIN = float(os.getenv("INITIAL_MARGIN_USD", "50"))
DCA_MARGIN = float(os.getenv("DCA_MARGIN_USD", "50"))
STATE_FILE = "state.json"
FAPI = "https://fapi.binance.com"

def utc_now():
    return datetime.now(timezone.utc)

def get_klines(interval, limit=500):
    url = "https://www.okx.com/api/v5/market/history-candles"

    bar_map = {
        "15m": "15m",
        "1h": "1H",
        "1d": "1Dutc",
        "1w": "1Wutc",
    }

    bar = bar_map.get(interval)
    if bar is None:
        raise ValueError(f"Unsupported interval: {interval}")

    rows = []
    after = None

    # OKX trả tối đa 300 nến mỗi lần gọi
    while len(rows) < limit:
        params = {
            "instId": "BTC-USDT-SWAP",
            "bar": bar,
            "limit": str(min(300, limit - len(rows))),
        }

        if after is not None:
            params["after"] = after

        response = requests.get(url, params=params, timeout=20)

        if not response.ok:
            print("OKX HTTP status:", response.status_code)
            print("OKX response:", response.text[:1000])
            response.raise_for_status()

        payload = response.json()

        if payload.get("code") != "0":
            raise RuntimeError(
                f"OKX API error: {payload.get('msg')}"
            )

        batch = payload.get("data", [])
        if not batch:
            break

        rows.extend(batch)

        # Dữ liệu trả về từ mới đến cũ.
        # Lấy timestamp của nến cũ nhất để truy vấn tiếp.
        oldest_ts = batch[-1][0]

        if len(batch) < 2 or len(rows) >= limit:
            break

        after = oldest_ts

    if not rows:
        raise RuntimeError("OKX returned no candle data")

    # Loại nến trùng và sắp xếp thời gian tăng dần
    unique_rows = {row[0]: row for row in rows}
    rows = sorted(unique_rows.values(), key=lambda row: int(row[0]))

    df = pd.DataFrame(
        rows,
        columns=[
            "open_time", "open", "high", "low", "close",
            "volume_contracts", "volume", "quote_volume", "confirm"
        ],
    )

    for c in ["open", "high", "low", "close", "volume", "quote_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    df["open_time"] = pd.to_datetime(
        pd.to_numeric(df["open_time"]),
        unit="ms",
        utc=True,
    )

    duration_map = {
        "15m": pd.Timedelta(minutes=15),
        "1h": pd.Timedelta(hours=1),
        "1d": pd.Timedelta(days=1),
        "1w": pd.Timedelta(weeks=1),
    }

    df["close_time"] = (
        df["open_time"] + duration_map[interval]
    )

    # Chỉ dùng nến đã đóng theo cờ xác nhận của OKX
    # và thời gian đóng nến.
    now = pd.Timestamp.now(tz="UTC")
    df = df.loc[
        (df["confirm"].astype(str) == "1")
        & (df["close_time"] <= now)
    ].copy()

    return df.tail(limit).reset_index(drop=True)
    

    

def rma(series, length):
    """Wilder RMA with an SMA seed, close to Pine ta.rma()."""
    x = pd.Series(series, dtype="float64")
    out = pd.Series(np.nan, index=x.index, dtype="float64")
    valid = x.dropna()
    if len(valid) < length:
        return out
    first_pos = valid.index[length - 1]
    seed = x.loc[valid.index[:length]].mean()
    out.loc[first_pos] = seed
    alpha = 1.0 / length
    started = False
    prev = np.nan
    for idx in x.index:
        val = x.loc[idx]
        if idx == first_pos:
            prev = seed
            started = True
        elif started:
            if pd.isna(val):
                out.loc[idx] = prev
            else:
                prev = (prev * (length - 1) + val) / length
                out.loc[idx] = prev
    return out

def add_indicators(df):
    d = df.copy()
    close = d["close"]
    prev_close = close.shift(1)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = rma(gain, RSI_LEN)
    avg_loss = rma(loss, RSI_LEN)
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi"] = 100 - (100 / (1 + rs))
    d.loc[(avg_loss == 0) & (avg_gain > 0), "rsi"] = 100
    d.loc[(avg_loss == 0) & (avg_gain == 0), "rsi"] = 50

    high, low = d["high"], d["low"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=d.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=d.index)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    atr = rma(tr, DMI_LEN)
    plus_di = 100 * rma(plus_dm, DMI_LEN) / atr.replace(0, np.nan)
    minus_di = 100 * rma(minus_dm, DMI_LEN) / atr.replace(0, np.nan)
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    d["plus_di"] = plus_di
    d["minus_di"] = minus_di
    d["adx"] = rma(dx, ADX_SMOOTH)
    return d

def load_state():
    default = {
        "last_processed_open": None,
        "side": None,
        "qty": 0.0,
        "avg_entry": 0.0,
        "dca_used": False,
        "entry_time": None,
        "last_event": None,
        "last_closed": None
    }
    if not os.path.exists(STATE_FILE):
        return default
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            saved = json.load(f)
        default.update(saved)
        return default
    except Exception:
        return default

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)

def telegram_send(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID secret.")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=20)
    r.raise_for_status()
    payload = r.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error: {payload}")

def fmt_price(x):
    return f"{x:,.2f}"

def send_event(kind, side, price, candle_time, roe=None, note=""):
    emoji = {"LONG ENTRY":"🟢", "SHORT ENTRY":"🔴", "DCA":"🟠",
             "TAKE PROFIT":"✅", "SOFT SL":"⚠️", "EMERGENCY SL":"🚨"}.get(kind, "ℹ️")
    lines = [
        f"{emoji} BTCUSDT FUTURES — {kind}",
        f"Chiều: {side or 'FLAT'}",
        f"Giá tham chiếu: {fmt_price(float(price))}",
        f"Nến M15 (UTC): {candle_time.strftime('%Y-%m-%d %H:%M')}",
        "Chế độ: TÍN HIỆU MÔ PHỎNG — BOT KHÔNG ĐẶT LỆNH"
    ]
    if roe is not None:
        lines.append(f"ROE ước tính tại giá đóng nến: {roe:.2f}%")
    if note:
        lines.append(note)
    lines.append("Người nhận tự xác minh giá và quản lý rủi ro.")
    telegram_send("\n".join(lines))

def roe_for(side, avg_entry, close):
    if not side or avg_entry <= 0:
        return None
    if side == "LONG":
        return ((close - avg_entry) / avg_entry) * LEVERAGE * 100
    return ((avg_entry - close) / avg_entry) * LEVERAGE * 100


def send_status_report(state, price, candle_time):
    side = state.get("side", "FLAT")
    last_closed = state.get("last_closed")

    lines = [
        "📊 BTCUSDT — BÁO CÁO TRẠNG THÁI",
        f"🕒 Thời gian nến: {candle_time}",
        f"💰 Giá tham chiếu: {fmt_price(price)}",
        "",
    ]

    if side in ("LONG", "SHORT"):
        avg_entry = float(state.get("avg_entry") or 0)
        qty = float(state.get("qty") or 0)
        dca_used = bool(state.get("dca_used", False))
        margin_used = INITIAL_MARGIN + (
            DCA_MARGIN if dca_used else 0
        )

        if avg_entry > 0 and qty > 0:
            if side == "LONG":
                pnl = (price - avg_entry) * qty
            else:
                pnl = (avg_entry - price) * qty

            roe = (
                pnl / margin_used * 100
                if margin_used > 0
                else 0
            )

            tp_price = threshold_price(
                side, avg_entry, 5.0
            )
            soft_sl_price = threshold_price(
                side, avg_entry, -35.0
            )
            emergency_sl_price = threshold_price(
                side, avg_entry, -90.0
            )

            lines.extend([
                f"📌 Vị thế hiện tại: {side}",
                f"Giá vào trung bình: {fmt_price(avg_entry)}",
                f"Số lượng: {qty:.8f} BTC",
                f"PnL chưa thực hiện (ước tính): {pnl:+.4f} USDT",
                f"ROE trên margin mô phỏng: {roe:+.2f}%",
                f"DCA đã dùng: {'Có' if dca_used else 'Chưa'}",
                f"Margin mô phỏng: {margin_used:.2f} USDT",
                "TP: đang theo dõi điều kiện RSI và ROE của chiến lược.",
                "Soft SL: đang theo dõi ROE, ADX H1 và xác nhận 2 nến.",
                "Emergency SL: đang giám sát ngưỡng bảo vệ khẩn cấp.",
            ])
        else:
            lines.extend([
                f"📌 Vị thế hiện tại: {side}",
                "⚠️ Thiếu giá vào hoặc số lượng hợp lệ; chưa tính được PnL.",
            ])
    else:
        lines.extend([
            "📌 Vị thế hiện tại: FLAT",
            "Hiện không có vị thế mở trong state.json.",
        ])

    lines.extend(["", "🧾 VỊ THẾ ĐÃ ĐÓNG GẦN NHẤT"])

    if isinstance(last_closed, dict) and last_closed:
        closed_side = last_closed.get("side", "N/A")
        reason = last_closed.get("reason", "N/A")
        entry = last_closed.get("avg_entry")
        exit_price = last_closed.get("exit_price")
        pnl = last_closed.get("pnl_usd")
        closed_roe = last_closed.get("roe_pct")
        exit_time = last_closed.get("exit_time", "N/A")

        lines.extend([
            f"Hướng: {closed_side}",
            f"Lý do đóng: {reason}",
            f"Giá vào TB: {fmt_price(entry) if entry is not None else 'N/A'}",
            f"Giá đóng: {fmt_price(exit_price) if exit_price is not None else 'N/A'}",
            f"PnL đã đóng: {float(pnl):+.4f} USDT" if pnl is not None else "PnL đã đóng: N/A",
            f"ROE khi đóng: {float(closed_roe):+.2f}%" if closed_roe is not None else "ROE khi đóng: N/A",
            f"Thời gian đóng: {exit_time}",
        ])
    else:
        lines.append("Chưa có dữ liệu vị thế đã đóng.")

    telegram_send("\n".join(lines))
    
def threshold_price(side, avg_entry, abs_roe):
    move = abs(abs_roe) / (100.0 * LEVERAGE)
    return avg_entry * (1.0 - move) if side == "LONG" else avg_entry * (1.0 + move)

def main():
    if os.getenv("TEST_TELEGRAM", "").strip() == "1":
        telegram_send("✅ BTCUSDT Signal Bot: Telegram kết nối thành công!")
        print("Telegram test message sent successfully.")
        return

    m15_raw = get_klines(INTERVAL, 500)
    h1_raw = get_klines(H1_INTERVAL, 500)
    if len(m15_raw) < 100 or len(h1_raw) < 100:
        raise RuntimeError("Not enough closed candles to warm up indicators.")

    m15 = add_indicators(m15_raw).reset_index(drop=True)
    h1 = add_indicators(h1_raw).reset_index(drop=True)
    h1_by_open = {row.open_time: row for row in h1.itertuples(index=False)}
    h1_times = list(h1["open_time"])

    state = load_state()
    last_processed = state.get("last_processed_open")
    if last_processed:
        last_processed = pd.Timestamp(last_processed)
        if last_processed.tzinfo is None:
            last_processed = last_processed.tz_localize("UTC")
        else:
            last_processed = last_processed.tz_convert("UTC")
    else:
        # First deployment: start at the latest closed candle only.
        last_processed = m15.iloc[-2]["open_time"] if len(m15) > 1 else None

    new_rows = (
        m15[m15["open_time"] > last_processed]
        if last_processed is not None
        else m15.tail(1)
    )

    if new_rows.empty:
        latest = m15.iloc[-1]
        send_status_report(state, float(latest["close"]), latest["open_time"])
        print("No new candles; status report sent.")
        return

    events_sent = 0
    for idx, row in new_rows.iterrows():
        if pd.isna(row["rsi"]) or pd.isna(row["adx"]):
            state["last_processed_open"] = row["open_time"].isoformat()
            continue

        bucket = row["open_time"].floor("h")
        prior_h1_candidates = [t for t in h1_times if t < bucket]
        if not prior_h1_candidates:
            state["last_processed_open"] = row["open_time"].isoformat()
            continue

        h1row = h1_by_open[prior_h1_candidates[-1]]
        if (
            pd.isna(h1row.plus_di)
            or pd.isna(h1row.minus_di)
            or pd.isna(h1row.adx)
        ):
            state["last_processed_open"] = row["open_time"].isoformat()
            continue

        prev_rsi = m15.loc[idx - 1, "rsi"] if idx > 0 else np.nan
        if pd.isna(prev_rsi):
            state["last_processed_open"] = row["open_time"].isoformat()
            continue

        close = float(row["close"])
        high = float(row["high"])
        low = float(row["low"])
        candle_open = float(row["open"])
        rsi = float(row["rsi"])
        adx = float(row["adx"])
        h1_plus = float(h1row.plus_di)
        h1_minus = float(h1row.minus_di)
        h1_adx = float(h1row.adx)

        h1_bull = h1_plus > h1_minus and h1_adx > H1_ADX_ENTRY
        h1_bear = h1_minus > h1_plus and h1_adx > H1_ADX_ENTRY
        long_signal = (
            h1_bull and prev_rsi <= LONG_RSI_ENTRY and rsi > LONG_RSI_ENTRY
        )
        short_signal = (
            h1_bear and prev_rsi >= SHORT_RSI_ENTRY and rsi < SHORT_RSI_ENTRY
        )

        side = state.get("side")
        avg_entry = float(state.get("avg_entry") or 0.0)
        qty = float(state.get("qty") or 0.0)
        dca_used = bool(state.get("dca_used", False))
        roe = roe_for(side, avg_entry, close)
        exited = False
        close_reason = None

        # Exit priority: emergency SL, soft SL, then TP.
        if side:
            emergency_price = threshold_price(
                side, avg_entry, abs(EMERGENCY_SL_ROE)
            )
            soft_price = threshold_price(side, avg_entry, abs(SOFT_SL_ROE))
            emergency_hit = (
                (side == "LONG" and low <= emergency_price)
                or (side == "SHORT" and high >= emergency_price)
            )

            h1_long_reversal = h1_minus > h1_plus and h1_adx > SOFT_SL_ADX
            h1_short_reversal = h1_plus > h1_minus and h1_adx > SOFT_SL_ADX
            prev_row = m15.loc[idx - 1] if idx > 0 else None
            two_bear = (
                candle_open > close
                and prev_row is not None
                and float(prev_row["close"]) < float(prev_row["open"])
            )
            two_bull = (
                close > candle_open
                and prev_row is not None
                and float(prev_row["close"]) > float(prev_row["open"])
            )
            soft_hit = (
                (
                    side == "LONG"
                    and low <= soft_price
                    and h1_long_reversal
                    and two_bear
                )
                or (
                    side == "SHORT"
                    and high >= soft_price
                    and h1_short_reversal
                    and two_bull
                )
            )
            tp_hit = (
                side == "LONG"
                and rsi >= LONG_RSI_TP
                and roe is not None
                and roe >= TP_ROE
            ) or (
                side == "SHORT"
                and rsi <= SHORT_RSI_TP
                and roe is not None
                and roe >= TP_ROE
            )

            if emergency_hit:
                close_reason = "EMERGENCY SL"
                send_event(
                    close_reason, side, close, row["open_time"], roe,
                    f"Mức ngưỡng tham chiếu: {fmt_price(emergency_price)}",
                )
                events_sent += 1
                exited = True
            elif soft_hit:
                close_reason = "SOFT SL"
                send_event(
                    close_reason, side, close, row["open_time"], roe,
                    f"Mức ngưỡng tham chiếu: {fmt_price(soft_price)}",
                )
                events_sent += 1
                exited = True
            elif tp_hit:
                close_reason = "TAKE PROFIT"
                tp_price = threshold_price(side, avg_entry, TP_ROE)
                tp_rsi = LONG_RSI_TP if side == "LONG" else SHORT_RSI_TP
                send_event(
                    close_reason, side, close, row["open_time"], roe,
                    f"Điều kiện TP đã đạt: RSI {'≥' if side == 'LONG' else '≤'} {tp_rsi:g}, "
                    f"ROE ≥ +{TP_ROE:g}%. Mức giá tham chiếu TP: {fmt_price(tp_price)}",
                )
                events_sent += 1
                exited = True

            if exited:
                closed_qty = qty
                closed_avg = avg_entry
                closed_margin = INITIAL_MARGIN + (
                    DCA_MARGIN if dca_used else 0
                )
                realized_pnl = (
                    (close - closed_avg) * closed_qty
                    if side == "LONG"
                    else (closed_avg - close) * closed_qty
                )
                closed_roe = (
                    realized_pnl / closed_margin * 100
                    if closed_margin > 0
                    else 0
                )
                state["last_closed"] = {
                    "side": side,
                    "reason": close_reason,
                    "avg_entry": closed_avg,
                    "exit_price": close,
                    "qty": closed_qty,
                    "pnl_usd": realized_pnl,
                    "roe_pct": closed_roe,
                    "entry_time": state.get("entry_time"),
                    "exit_time": row["open_time"].strftime(
                        "%Y-%m-%d %H:%M UTC"
                    ),
                }
                state.update(
                    {
                        "side": None,
                        "qty": 0.0,
                        "avg_entry": 0.0,
                        "dca_used": False,
                        "entry_time": None,
                    }
                )
                side, avg_entry, qty, dca_used = None, 0.0, 0.0, False

        # Do not open a new position on the same candle that closed one.
        if side is None and not exited:
            if long_signal:
                new_qty = INITIAL_MARGIN * LEVERAGE / close
                state.update(
                    {
                        "side": "LONG",
                        "qty": new_qty,
                        "avg_entry": close,
                        "dca_used": False,
                        "entry_time": row["open_time"].isoformat(),
                    }
                )
                send_event(
                    "LONG ENTRY", "LONG", close, row["open_time"],
                    note=f"Khối lượng mô phỏng: {new_qty:.8f} BTC",
                )
                events_sent += 1
            elif short_signal:
                new_qty = INITIAL_MARGIN * LEVERAGE / close
                state.update(
                    {
                        "side": "SHORT",
                        "qty": new_qty,
                        "avg_entry": close,
                        "dca_used": False,
                        "entry_time": row["open_time"].isoformat(),
                    }
                )
                send_event(
                    "SHORT ENTRY", "SHORT", close, row["open_time"],
                    note=f"Khối lượng mô phỏng: {new_qty:.8f} BTC",
                )
                events_sent += 1

        elif side and not exited and not dca_used:
            current_roe = roe_for(side, avg_entry, close)
            if side == "LONG":
                dca_cond = (
                    current_roe is not None
                    and current_roe <= DCA_ROE
                    and adx > DCA_ADX
                    and rsi > LONG_DCA_RSI
                    and rsi > prev_rsi
                )
            else:
                dca_cond = (
                    current_roe is not None
                    and current_roe <= DCA_ROE
                    and adx > DCA_ADX
                    and rsi < SHORT_DCA_RSI
                    and rsi < prev_rsi
                )

            if dca_cond:
                add_qty = DCA_MARGIN * LEVERAGE / close
                total_qty = qty + add_qty
                if total_qty > 0:
                    new_avg = (
                        (avg_entry * qty) + (close * add_qty)
                    ) / total_qty
                    state.update(
                        {
                            "qty": total_qty,
                            "avg_entry": new_avg,
                            "dca_used": True,
                        }
                    )
                    send_event(
                        "DCA", side, close, row["open_time"], current_roe,
                        f"Khối lượng DCA mô phỏng: {add_qty:.8f} BTC; "
                        f"giá vốn mô phỏng sau DCA: {fmt_price(new_avg)}",
                    )
                    events_sent += 1

        state["last_processed_open"] = row["open_time"].isoformat()

    save_state(state)
    latest = m15.iloc[-1]
    send_status_report(state, float(latest["close"]), latest["open_time"])

    print(
        f"Processed {len(new_rows)} candle(s); "
        f"sent {events_sent} Telegram event(s)."
    )
    print(
        f"Status report sent; side={state.get('side')}, "
        f"dca_used={state.get('dca_used')}, "
        f"last={state.get('last_processed_open')}"
    )


if __name__ == "__main__":
    main()
    
          
