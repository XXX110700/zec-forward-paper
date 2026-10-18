# ZEC-USDT-SWAP V6.3 — GitHub Actions low-frequency forward paper test
# Public OKX market data only. No API key, account access, or automatic orders.

import csv
import json
from bisect import bisect_right
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
import requests

BASE = "https://www.okx.com"
INST = "ZEC-USDT-SWAP"
TIMEOUT = 15
ATR_PERIOD = 14
ST_MULT = 3.0

REQUIRED = 5
TP1_R = 1.2
TP2_R = 2.0
FEE_RATE = 0.0005
SLIPPAGE_RATE = 0.0002

STATE_FILE = Path(__file__).with_name("zec_v63_paper_state.json")
CSV_FILE = Path(__file__).with_name("zec_v63_trades.csv")

BAR15 = 15 * 60 * 1000
BAR1H = 60 * 60 * 1000
BAR4H = 4 * 60 * 60 * 1000


def api(path, params):
    r = requests.get(
        BASE + path, params=params, timeout=TIMEOUT,
        headers={"User-Agent": "zec-forward-paper/6.3"}
    )
    r.raise_for_status()
    j = r.json()
    if j.get("code") != "0":
        raise RuntimeError(f"OKX public API error: {j.get('code')} {j.get('msg')}")
    return j["data"]


def recent_closed(bar, limit=300):
    rows = api("/api/v5/market/candles",
               {"instId": INST, "bar": bar, "limit": str(limit)})
    out = []
    for x in rows:
        # OKX candles timestamp the OPEN of the bar.
        # Field 8 confirm=1 means the candle is completed.
        if len(x) >= 9 and str(x[8]) == "1":
            out.append((int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4])))
    dedup = {x[0]: x for x in out}
    return [dedup[k] for k in sorted(dedup)]


def rsi(values, period=14):
    out = [None] * len(values)
    if len(values) <= period:
        return out
    gains, losses = [], []
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
    ag, al = mean(gains), mean(losses)
    out[period] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period + 1, len(values)):
        ch = values[i] - values[i - 1]
        ag = (ag * (period - 1) + max(ch, 0)) / period
        al = (al * (period - 1) + max(-ch, 0)) / period
        out[i] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def atr(candles, period=14):
    out = [None] * len(candles)
    trs = []
    for i, (_, o, h, l, c) in enumerate(candles):
        tr = h - l if i == 0 else max(
            h - l, abs(h - candles[i - 1][4]), abs(l - candles[i - 1][4])
        )
        trs.append(tr)
    if len(trs) <= period:
        return out
    a = mean(trs[1:period + 1])
    out[period] = a
    for i in range(period + 1, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def supertrend(candles, period=14, mult=3.0):
    a = atr(candles, period)
    st = [None] * len(candles)
    direction = [None] * len(candles)
    fub = [None] * len(candles)
    flb = [None] * len(candles)
    for i in range(period, len(candles)):
        _, o, h, l, c = candles[i]
        hl2 = (h + l) / 2
        bub, blb = hl2 + mult * a[i], hl2 - mult * a[i]
        if i == period:
            fub[i], flb[i] = bub, blb
        else:
            pc = candles[i - 1][4]
            fub[i] = bub if (bub < fub[i - 1] or pc > fub[i - 1]) else fub[i - 1]
            flb[i] = blb if (blb > flb[i - 1] or pc < flb[i - 1]) else flb[i - 1]
        if i == period:
            st[i], direction[i] = ((fub[i], -1) if c <= fub[i] else (flb[i], 1))
        elif st[i - 1] == fub[i - 1]:
            st[i], direction[i] = ((fub[i], -1) if c <= fub[i] else (flb[i], 1))
        else:
            st[i], direction[i] = ((flb[i], 1) if c >= flb[i] else (fub[i], -1))
    return st, direction


def score_tf(candles, i):
    if i < 25:
        return 0
    closes = [x[4] for x in candles]
    m5 = mean(closes[i-4:i+1])
    m10 = mean(closes[i-9:i+1])
    m20 = mean(closes[i-19:i+1])
    c = closes[i]
    return ((1 if c > m20 else -1)
            + (1 if m5 > m10 else -1)
            + (1 if m10 > m20 else -1))


def index_at_or_before(candles, ts):
    return bisect_right([x[0] for x in candles], ts) - 1


def last_closed_index(candles, decision_ts, bar_ms):
    # HTF candle open must be <= decision time - its full duration.
    return index_at_or_before(candles, decision_ts - bar_ms)


def confirmations(side, i15, i1, i4, c15, c1h, c4h, r15, d1, d4):
    if min(i15, i1, i4) < 25 or r15[i15] is None:
        return 0
    s1, s4 = score_tf(c1h, i1), score_tf(c4h, i4)
    close = c15[i15][4]
    prior = c15[i15-20:i15]
    support = min(x[3] for x in prior)
    resist = max(x[2] for x in prior)
    if side == "short":
        checks = [s1 <= -3, s4 <= -2, d1[i1] == -1, d4[i4] == -1,
                  close < support, r15[i15] <= 48]
    else:
        checks = [s1 >= 3, s4 >= 2, d1[i1] == 1, d4[i4] == 1,
                  close > resist, r15[i15] >= 52]
    return sum(bool(x) for x in checks)


def fmt_ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def default_state():
    return {
        "version": "V6.3",
        "last_processed_15m_ts": None,
        "pending_signal": None,
        "open_trade": None,
        "closed_trades": [],
    }


def load_state():
    if STATE_FILE.exists():
        try:
            s = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            base = default_state()
            base.update(s)
            return base
        except Exception:
            pass
    return default_state()


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def append_csv(row):
    exists = CSV_FILE.exists()
    with CSV_FILE.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow([
                "event", "side", "signal_utc", "entry_utc", "entry", "sl", "tp1", "tp2",
                "exit_utc", "exit_price", "reason", "net_r", "long_confirm", "short_confirm",
            ])
        w.writerow(row)


def maybe_enter(state, bar):
    p = state.get("pending_signal")
    if not p or state.get("open_trade"):
        return
    if bar[0] <= int(p["signal_ts"]):
        return
    entry = float(bar[1])
    risk = max(float(p["atr"]) * 1.5, entry * 0.004)
    side = p["side"]
    if side == "long":
        sl, tp1, tp2 = entry-risk, entry+risk*TP1_R, entry+risk*TP2_R
    else:
        sl, tp1, tp2 = entry+risk, entry-risk*TP1_R, entry-risk*TP2_R
    state["open_trade"] = {
        "side": side, "signal_ts": p["signal_ts"], "entry_candle_ts": bar[0],
        "entry": entry, "risk": risk, "sl": sl, "tp1": tp1, "tp2": tp2,
        "long_confirm": p["long_confirm"], "short_confirm": p["short_confirm"],
    }
    state["pending_signal"] = None
    append_csv(["ENTRY", side, fmt_ts(int(p["signal_ts"])), fmt_ts(bar[0]), entry,
                sl, tp1, tp2, "", "", "", "", p["long_confirm"], p["short_confirm"]])
    print(f"  模拟入场 {side.upper()} @ {entry:.4f}")


def maybe_exit(state, bar):
    t = state.get("open_trade")
    if not t or bar[0] < int(t["entry_candle_ts"]):
        return
    side = t["side"]
    entry, risk = float(t["entry"]), float(t["risk"])
    sl, tp1, tp2 = float(t["sl"]), float(t["tp1"]), float(t["tp2"])
    _, o, h, l, c = bar
    if side == "long":
        hit_sl, hit1, hit2 = l <= sl, h >= tp1, h >= tp2
    else:
        hit_sl, hit1, hit2 = h >= sl, l <= tp1, l <= tp2

    # Conservative same-candle assumption: SL first.
    if hit_sl:
        gross_r, exit_price, reason = -1.0, sl, "SL"
    elif hit2:
        gross_r, exit_price, reason = TP2_R, tp2, "TP2"
    elif hit1:
        gross_r, exit_price, reason = TP1_R, tp1, "TP1"
    else:
        return

    cost_price = entry * 2 * (FEE_RATE + SLIPPAGE_RATE)
    net_r = gross_r - cost_price / risk
    t.update({"exit_ts": bar[0], "exit_price": exit_price,
              "reason": reason, "gross_r": gross_r, "net_r": round(net_r, 6)})
    state["closed_trades"].append(t)
    append_csv(["EXIT", side, fmt_ts(int(t["signal_ts"])), fmt_ts(int(t["entry_candle_ts"])),
                entry, sl, tp1, tp2, fmt_ts(bar[0]), exit_price, reason,
                round(net_r, 6), t.get("long_confirm"), t.get("short_confirm")])
    state["open_trade"] = None
    print(f"  模拟退出 {side.upper()} {reason} {net_r:+.3f}R")


def stats(closed):
    vals = [float(t["net_r"]) for t in closed]
    if not vals:
        return 0, 0.0, 0.0, 0.0, 0.0, 0
    eq = peak = dd = 0.0
    streak = max_streak = 0
    for v in vals:
        eq += v
        peak = max(peak, eq)
        dd = max(dd, peak-eq)
        if v < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return (len(vals), 100*sum(v > 0 for v in vals)/len(vals),
            sum(vals), sum(vals)/len(vals), dd, max_streak)


def main():
    print("=" * 66)
    print("ZEC V6.3 GitHub 低耗前向模拟")
    print("公开行情只读 | 无API Key | 无账户访问 | 不会自动下单")
    print(f"冻结规则 {REQUIRED}/6 | TP {TP1_R:.1f}R/{TP2_R:.1f}R")
    print("=" * 66)

    state = load_state()
    c15 = recent_closed("15m")
    c1h = recent_closed("1H")
    c4h = recent_closed("4H")
    if len(c15) < 80 or len(c1h) < 40 or len(c4h) < 40:
        raise RuntimeError("已收盘K线数据不足。")

    r15 = rsi([x[4] for x in c15])
    a15 = atr(c15, ATR_PERIOD)
    _, d1 = supertrend(c1h, ATR_PERIOD, ST_MULT)
    _, d4 = supertrend(c4h, ATR_PERIOD, ST_MULT)

    last = state.get("last_processed_15m_ts")
    if last is None:
        # First deployment: start at latest completed bar, avoiding historical pseudo-forward trades.
        indices = [len(c15)-1]
        print("首次运行：从最新已收盘15m开始，不倒灌历史模拟交易。")
    else:
        indices = [i for i, x in enumerate(c15) if x[0] > int(last)]
        if not indices:
            print("没有新的15m已收盘K线。")
            save_state(state)
            return

    print(f"本次顺序处理 {len(indices)} 根新15m K线。")

    for i in indices:
        bar = c15[i]
        ts = bar[0]

        # Old pending signal enters at this bar's open, then this bar may hit exit levels.
        maybe_enter(state, bar)
        maybe_exit(state, bar)

        decision_ts = ts + BAR15
        i1 = last_closed_index(c1h, decision_ts, BAR1H)
        i4 = last_closed_index(c4h, decision_ts, BAR4H)

        cl = cs = 0
        if i >= 25 and i1 >= 25 and i4 >= 25 and a15[i] is not None:
            cl = confirmations("long", i, i1, i4, c15, c1h, c4h, r15, d1, d4)
            cs = confirmations("short", i, i1, i4, c15, c1h, c4h, r15, d1, d4)

        print(f"{fmt_ts(ts)} | 多 {cl}/6 | 空 {cs}/6")

        # Only create a new signal when flat and no prior pending signal remains.
        if not state.get("open_trade") and not state.get("pending_signal"):
            side = "short" if cs >= REQUIRED and cs > cl else (
                "long" if cl >= REQUIRED and cl > cs else None
            )
            if side:
                state["pending_signal"] = {
                    "side": side, "signal_ts": ts, "signal_close": bar[4],
                    "atr": a15[i], "long_confirm": cl, "short_confirm": cs,
                }
                append_csv(["SIGNAL", side, fmt_ts(ts), "", "", "", "", "",
                            "", "", "", "", cl, cs])
                print(f"  新信号 {side.upper()}，下一根15m开盘模拟入场")

        state["last_processed_15m_ts"] = ts

    n, win, total, avg, dd, streak = stats(state["closed_trades"])
    print(f"\n累计 {n}笔 | 胜率 {win:.1f}% | {total:+.2f}R | 平均 {avg:+.3f}R")
    print(f"最大回撤 {dd:.2f}R | 最大连续亏损 {streak}笔")
    if state.get("open_trade"):
        t = state["open_trade"]
        print(f"当前模拟仓位: {t['side'].upper()} @ {t['entry']:.4f}")
    elif state.get("pending_signal"):
        print("当前状态: 有待入场信号")
    else:
        print("当前状态: 空仓等待")
state["long_confirm"] = cl
state["short_confirm"] = cs
state["max_drawdown"] = dd
state["updated_at"] = fmt_ts(state["last_processed_15m_ts"])
    save_state(state)


if __name__ == "__main__":
    main()
