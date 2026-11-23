"""AI 供应链周期报告的外部数据：行情（yfinance）、SEC XBRL（capex / OCF / 收入）、台湾月营收（FinMind）。
各自写 data/cycle/*.json，失败时保留旧文件（页面标「数据截至」）。
用法：python3 src/cycle_data.py [prices] [sec] [tw]   不带参数 = 全部
"""
import json, sys, time, math
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml, requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "cycle"
OUT.mkdir(parents=True, exist_ok=True)
CFG = yaml.safe_load((ROOT / "cycle" / "baskets.yaml").read_text())
SETTINGS = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text())


def now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save(name, obj):
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))


# ---------------- 行情 ----------------
def prices():
    import yfinance as yf, pandas as pd
    tickers = sorted({t for b in CFG["baskets"] for t in b["tickers"]})
    df = None
    for attempt in range(3):
        try:
            df = yf.download(tickers, period="2y", progress=False, auto_adjust=True, threads=True)["Close"]
            if df is not None and df.dropna(how="all").shape[0] > 200:
                break
        except Exception as ex:
            print("yfinance 失败", ex)
        time.sleep(30)
    if df is None or df.dropna(how="all").shape[0] < 200:
        print("行情未取到，保留旧文件"); return False
    df = df.dropna(how="all")
    # 最后一行若多数美股为空（亚洲已开盘、美股未开），各自用最后有效值
    stats = {}
    for t in tickers:
        s = df[t].dropna()
        if len(s) < 60:
            continue
        last = float(s.iloc[-1])
        def chg(n):
            return round((last / float(s.iloc[-1 - n]) - 1) * 100, 2) if len(s) > n else None
        y = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
        ytd = s[s.index.year == s.index[-1].year]
        hi = float(y.max())
        ma50 = float(s.tail(50).mean()); ma200 = float(s.tail(200).mean()) if len(s) >= 200 else None
        stats[t] = {"last": round(last, 2), "asof": s.index[-1].strftime("%Y-%m-%d"),
                    "d1": chg(1), "d5": chg(5), "m1": chg(21), "m3": chg(63),
                    "ytd": round((last / float(ytd.iloc[0]) - 1) * 100, 1) if len(ytd) > 1 else None,
                    "y1": chg(252) if len(s) > 252 else None,
                    "dd": round((last / hi - 1) * 100, 1), "hi_date": y.idxmax().strftime("%Y-%m-%d"),
                    "a50": last > ma50, "a200": (last > ma200) if ma200 else None}
    # 等权篮子指数：并集日历前向填充，日收益等权平均，近 1 年重定基 100
    ff = df.ffill()
    ret = ff.pct_change()
    start = ff.index[-1] - pd.Timedelta(days=365)
    baskets = []
    for b in CFG["baskets"]:
        ts = [t for t in b["tickers"] if t in stats]
        if not ts:
            continue
        r = ret[ts].mean(axis=1, skipna=True)
        idx = (1 + r[r.index >= start].fillna(0)).cumprod() * 100
        wk = idx.iloc[::5]
        if wk.index[-1] != idx.index[-1]:
            wk = pd.concat([wk, idx.iloc[[-1]]])
        def bchg(n):
            return round((float(idx.iloc[-1]) / float(idx.iloc[-1 - n]) - 1) * 100, 1) if len(idx) > n else None
        baskets.append({"id": b["id"], "layer": b["layer"], "zh": b["zh"], "tickers": ts,
                        "series": [[d.strftime("%Y-%m-%d"), round(float(v), 1)] for d, v in wk.items()],
                        "last": round(float(idx.iloc[-1]), 1), "d5": bchg(5), "m1": bchg(21), "m3": bchg(63),
                        "dd": round((float(idx.iloc[-1]) / float(idx.max()) - 1) * 100, 1),
                        "hi_date": idx.idxmax().strftime("%Y-%m-%d"),
                        "b50": round(sum(1 for t in ts if stats[t]["a50"]) / len(ts) * 100),
                        "b200": round(sum(1 for t in ts if stats[t]["a200"]) / len(ts) * 100),
                        "med_dd": sorted(stats[t]["dd"] for t in ts)[len(ts) // 2]})
    save("prices.json", {"generated_at": now_z(), "asof": max(v["asof"] for v in stats.values()),
                         "names": CFG.get("names", {}), "tickers": stats, "baskets": baskets})
    print("行情 OK：", len(stats), "只，", len(baskets), "个篮子")
    return True


# ---------------- SEC XBRL ----------------
CAPEX_TAGS = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"]
OCF_TAGS = ["NetCashProvidedByUsedInOperatingActivities"]
REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]


def quarterly(facts, tags):
    """把 XBRL 的年初至今累计值拆成单季。返回 {period_end: value}。"""
    best = {}
    for tag in tags:
        rows = facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])
        by = {}
        for r in rows:
            if "start" not in r:
                continue
            s = datetime.strptime(r["start"], "%Y-%m-%d"); e = datetime.strptime(r["end"], "%Y-%m-%d")
            by[(r["start"], r["end"])] = (r["val"], (e - s).days, r.get("filed", ""))
        q = {}
        for (s, e), (v, days, _) in by.items():
            if 80 <= days <= 100:
                q[e] = v
        # 用累计值相减补出缺的单季（现金流量表常只报 YTD）
        for (s, e), (v, days, _) in by.items():
            if days > 100 and e not in q:
                prev = [(e2, v2, d2) for (s2, e2), (v2, d2, _) in by.items() if s2 == s and e2 < e and 80 <= days - d2 <= 100]
                if prev:
                    e2, v2, _ = max(prev, key=lambda x: x[2])
                    q[e] = v - v2
        for e, v in q.items():
            if e not in best or tag == tags[0]:
                best.setdefault(e, v)
        if len(best) >= 8 and max(best) >= "2026-06":
            break
    return best


def sec():
    ua = {"User-Agent": SETTINGS["sec_user_agent"]}
    out = []
    for c in CFG["sec"]:
        try:
            r = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c['cik']:010d}.json", headers=ua, timeout=40)
            r.raise_for_status()
            f = r.json()
        except Exception as ex:
            print("SEC 失败", c["id"], ex); continue
        cap, ocf, rev = quarterly(f, CAPEX_TAGS), quarterly(f, OCF_TAGS), quarterly(f, REV_TAGS)
        ends = sorted(set(cap) & set(ocf))[-10:]
        rows = [{"end": e, "capex": cap[e], "ocf": ocf[e], "rev": rev.get(e)} for e in ends]
        out.append({"id": c["id"], "zh": c["zh"], "quarters": rows})
        print(c["id"], [(x["end"], round(x["capex"] / 1e9, 1), round(x["ocf"] / 1e9, 1), round((x["rev"] or 0) / 1e9, 1)) for x in rows[-3:]])
        time.sleep(0.3)
    if len(out) >= 3:
        save("sec.json", {"generated_at": now_z(), "companies": out})
        return True
    return False


# ---------------- 台湾月营收 ----------------
def tw():
    start = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-01")
    out = []
    for c in CFG["tw_revenue"]:
        try:
            r = requests.get("https://api.finmindtrade.com/api/v4/data",
                             params={"dataset": "TaiwanStockMonthRevenue", "data_id": c["id"], "start_date": start}, timeout=30)
            d = r.json().get("data", [])
        except Exception as ex:
            print("FinMind 失败", c["id"], ex); continue
        m = {(x["revenue_year"], x["revenue_month"]): x["revenue"] for x in d}
        ks = sorted(m)
        rows = []
        for (y, mo) in ks[-13:]:
            prev = m.get((y - 1, mo))
            rows.append({"ym": f"{y}-{mo:02d}", "rev": m[(y, mo)], "yoy": round((m[(y, mo)] / prev - 1) * 100, 1) if prev else None})
        def yoy3(i):   # 以第 i 个月结尾的 3 个月合计同比
            seg = ks[i - 2:i + 1]
            a = sum(m[k] for k in seg); b = [m.get((k[0] - 1, k[1])) for k in seg]
            return round((a / sum(b) - 1) * 100, 1) if all(b) else None
        n = len(ks) - 1
        out.append({**c, "months": rows, "yoy3": yoy3(n) if n >= 14 else None, "yoy3_prev": yoy3(n - 3) if n >= 17 else None,
                    "latest": rows[-1]["ym"] if rows else None})
        time.sleep(0.4)
    if len(out) >= 6:
        save("tw.json", {"generated_at": now_z(), "companies": out})
        print("台湾月营收 OK：", len(out), "家，最新", max(c["latest"] for c in out if c["latest"]))
        return True
    return False


FETCH = {"prices": prices, "sec": sec, "tw": tw}
MAX_AGE_H = {"prices": 6, "tw": 24, "sec": 24}   # 文件超过这么多小时才重取；月营收和财报一天查一次就够


def refresh_stale(force=False):
    """run.py 每次运行调用：只重取过期的数据，单项失败不影响其它项，旧文件保留。"""
    done = []
    for name, fn in FETCH.items():
        f = OUT / f"{name}.json"
        age_h = (time.time() - f.stat().st_mtime) / 3600 if f.exists() else 1e9
        if not force and age_h < MAX_AGE_H[name]:
            continue
        try:
            if fn():
                done.append(name)
        except Exception as ex:
            print(name, "失败：", ex)
    return done


if __name__ == "__main__":
    todo = sys.argv[1:] or ["prices", "sec", "tw"]
    for t in todo:
        try:
            FETCH[t]()
        except Exception as ex:
            print(t, "失败：", ex)
