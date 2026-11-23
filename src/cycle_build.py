"""AI 供应链周期实时报告：把账本（docs/data/graph.json）、行情、SEC、台湾月营收、调研读数拼成 cycle.json，
并渲染页面。输出：
  cycle/out/cycle.json            报告数据
  cycle/out/artifact.html         Artifact 用（数据内嵌、无 doctype）
  docs/cycle.html + docs/data/cycle.json   GitHub Pages 用（完整文档，读取 data/cycle.json），随 run.py 的 publish 推送
用法：python3 src/cycle_build.py
"""
import json, re, statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CY = ROOT / "cycle"
OUT = CY / "out"
DATA = ROOT / "data" / "cycle"
CFG = yaml.safe_load((CY / "baskets.yaml").read_text())
TH = CFG.get("thresholds", {})
KIND_ZH = {"order": "订单", "investment": "投资", "debt": "债务", "lease": "租赁", "guarantee": "担保", "vendor_financing": "供应商融资"}
RISKY_GROUPS = {"model_lab", "neocloud", "datacenter", "spv"}      # 非投资级 / 无评级为主的借款人
SUPPLIER_GROUPS = {"chip", "equipment"}
CUSTOMER_GROUPS = {"model_lab", "neocloud", "datacenter", "spv", "hyperscaler"}


def load(p, default=None):
    p = Path(p)
    return json.loads(p.read_text()) if p.exists() else default


def now_z():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def th(k, d):
    return TH.get(k, d)


def zb(v):
    """金额 → 中文计数（亿 / 万亿美元）。"""
    if not v:
        return "0"
    return f"{v / 1e12:.2f} 万亿美元" if abs(v) >= 1e12 else f"{v / 1e8:,.0f} 亿美元"


def worst(*st):
    order = {"off": 0, "na": 0, "watch": 1, "on": 2}
    return max(st, key=lambda s: order.get(s, 0)) if st else "na"


def build():
    g = load(ROOT / "docs" / "data" / "graph.json")
    px = load(DATA / "prices.json", {})
    sec = load(DATA / "sec.json", {})
    tw = load(DATA / "tw.json", {})
    nar = yaml.safe_load((CY / "narrative.yaml").read_text())
    readings = {k: load(CY / f"readings_{k}.json", []) for k in ("down", "mid", "up")}
    today = datetime.now(timezone.utc)
    ents = {e["id"]: e for e in g["entities"]}
    grp = lambda i: ents.get(i, {}).get("group", "other")
    nm = lambda i: ents.get(i, {}).get("name_zh") or ents.get(i, {}).get("name") or i
    live = [e for e in g["edges"] if not e.get("cancelled")]
    age = lambda e: (today.replace(tzinfo=None) - datetime.strptime(e["announced"][:10], "%Y-%m-%d")).days if e.get("announced") else 9999
    usd = lambda rows: sum(e["amount_usd"] or 0 for e in rows)
    slim = lambda e: {"id": e["id"], "kind": e["kind"], "kind_zh": KIND_ZH.get(e["kind"], e["kind"]), "from": nm(e["from_entity"]), "to": nm(e["to_entity"]),
                      "usd": e["amount_usd"], "date": e["announced"], "title": e["title_zh"], "tier": e["tier"], "status": e["status"],
                      "url": (e.get("sources") or [{}])[0].get("url")}

    # ---------- 资金：月度新增 ----------
    months = []
    cur = today.replace(day=1)
    for _ in range(15):
        months.append(cur.strftime("%Y-%m")); cur = (cur - timedelta(days=1)).replace(day=1)
    months = months[::-1]
    monthly = {m: {k: 0.0 for k in ("order", "investment", "debt", "lease", "guarantee")} for m in months}
    for e in live:
        m = (e.get("announced") or "")[:7]
        if m in monthly and e["kind"] in monthly[m]:
            monthly[m][e["kind"]] += e["amount_usd"] or 0
    monthly_rows = [{"ym": m, **{k: round(v / 1e9, 1) for k, v in monthly[m].items()}} for m in months]

    # ---------- 资金：借款人结构（债务 + 租赁，按季） ----------
    dq = defaultdict(lambda: {"ig": 0.0, "risky": 0.0, "other": 0.0})
    for e in live:
        if e["kind"] in ("debt", "lease") and e.get("announced"):
            a = e["announced"]; q = f"{a[:4]}Q{(int(a[5:7]) - 1) // 3 + 1}"
            gq = grp(e["from_entity"])
            dq[q]["ig" if gq == "hyperscaler" else "risky" if gq in RISKY_GROUPS else "other"] += e["amount_usd"] or 0
    debt_q = [{"q": q, **{k: round(v / 1e9, 1) for k, v in dq[q].items()}} for q in sorted(dq)[-6:]]
    cur_q = debt_q[-1] if debt_q else None
    risky_share = round(cur_q["risky"] / max(cur_q["ig"] + cur_q["risky"] + cur_q["other"], 1e-9) * 100) if cur_q else None
    d30 = usd([e for e in live if e["kind"] in ("debt", "lease") and age(e) <= 30])
    d_prev90 = usd([e for e in live if e["kind"] in ("debt", "lease") and 30 < age(e) <= 120]) / 3
    window_ratio = round(d30 / d_prev90, 2) if d_prev90 else None

    # ---------- 资金：供应商给客户的钱（循环融资） ----------
    circ = [e for e in live if grp(e["from_entity"]) in SUPPLIER_GROUPS and grp(e["to_entity"]) in CUSTOMER_GROUPS
            and e["kind"] in ("investment", "guarantee", "vendor_financing", "lease")]
    circ90 = [e for e in circ if age(e) <= 90]
    circ_prev90 = [e for e in circ if 90 < age(e) <= 180]

    # ---------- 资金：订单集中度 ----------
    buyers = defaultdict(float)
    for e in live:
        if e["kind"] in ("order", "lease"):
            buyers[e["from_entity"]] += e["amount_usd"] or 0
    tot_orders = sum(buyers.values())
    top_buyers = [{"name": nm(k), "usd": v, "share": round(v / tot_orders * 100, 1), "group": grp(k)} for k, v in sorted(buyers.items(), key=lambda x: -x[1])[:6]]
    lab_share = round(sum(v for k, v in buyers.items() if grp(k) == "model_lab") / tot_orders * 100) if tot_orders else None
    o90 = usd([e for e in live if e["kind"] == "order" and age(e) <= 90]); o_prev90 = usd([e for e in live if e["kind"] == "order" and 90 < age(e) <= 180])

    # ---------- 资金：撤单 / 取消（未被新协议取代的） ----------
    excl = {x["id"] for x in CFG.get("cancel_exclude", [])}
    cancels = [e for e in g["edges"] if e["status"] == "cancelled" and not e.get("superseded_by") and age(e) <= th("cancel_window_days", 90) and e["id"] not in excl]
    cancels.sort(key=lambda e: e["announced"], reverse=True)
    stress_kw = re.compile(r"下调|缩至|缩减|推迟|暂停|搁置|撤回|撤出|取消|违约|降级|cut|pull|delay|pause|default|downgrad", re.I)
    stress_cands = [c for c in g["candidates"] if stress_kw.search((c.get("title_zh") or "") + (c.get("title_en") or "")) and age(c) <= 45]

    recent = sorted([e for e in live if age(e) <= 14], key=lambda e: -(e["amount_usd"] or 0))[:10]

    # ---------- 现金流：capex / 经营现金流 ----------
    def calq(end):
        y, m = int(end[:4]), int(end[5:7])
        return f"{y}Q{(m - 1) // 3 + 1}" if m not in (2, 5, 8, 11) else f"{y}Q{(m + 1) // 3}"
    agg = defaultdict(lambda: {"capex": 0.0, "ocf": 0.0, "n": 0, "who": []})
    capex_cos = []
    for c in sec.get("companies", []):
        rows = []
        for r in c["quarters"]:
            q = calq(r["end"])
            agg[q]["capex"] += r["capex"]; agg[q]["ocf"] += r["ocf"]; agg[q]["n"] += 1; agg[q]["who"].append(c["id"])
            rows.append({"q": q, "end": r["end"], "capex": round(r["capex"] / 1e9, 1), "ocf": round(r["ocf"] / 1e9, 1),
                         "fcf": round((r["ocf"] - r["capex"]) / 1e9, 1), "ratio": round(r["capex"] / r["ocf"] * 100) if r["ocf"] else None})
        capex_cos.append({"id": c["id"], "zh": c["zh"], "quarters": rows[-8:]})
    ncos = len(sec.get("companies", []))
    agg_rows = [{"q": q, "capex": round(v["capex"] / 1e9, 1), "ocf": round(v["ocf"] / 1e9, 1), "ratio": round(v["capex"] / v["ocf"] * 100)}
                for q, v in sorted(agg.items()) if v["n"] == ncos][-8:]
    cap_ratio = agg_rows[-1]["ratio"] if agg_rows else None
    neg_fcf = [c["zh"] for c in capex_cos if c["quarters"] and c["quarters"][-1]["fcf"] < 0]

    # ---------- 市场 ----------
    bk = {b["id"]: b for b in px.get("baskets", [])}
    lay = lambda L: [b for b in px.get("baskets", []) if b["layer"] == L]
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    mid_dd = avg([b["dd"] for b in lay("mid")]); up_dd = avg([b["dd"] for b in lay("up")]); down_dd = avg([b["dd"] for b in lay("down")])
    chain_t = [t for b in px.get("baskets", []) if b["layer"] in ("down", "mid", "up") for t in b["tickers"]]
    a200 = round(sum(1 for t in chain_t if px["tickers"][t]["a200"]) / len(chain_t) * 100) if chain_t else None
    a50 = round(sum(1 for t in chain_t if px["tickers"][t]["a50"]) / len(chain_t) * 100) if chain_t else None
    rot = round(avg([b["m3"] for b in lay("up")]) - avg([b["m3"] for b in lay("mid")]), 1) if lay("up") and lay("mid") else None
    hyg = px.get("tickers", {}).get("HYG", {})
    neo_dd = bk.get("neocloud", {}).get("dd")

    # ---------- 实物：台湾月营收 ----------
    twc = tw.get("companies", [])
    def tw_stat(L):
        rows = [c for c in twc if c["layer"] == L and c.get("yoy3") is not None and c.get("yoy3_prev") is not None]
        if not rows:
            return None
        return {"n": len(rows), "median_yoy3": round(statistics.median(c["yoy3"] for c in rows), 1),
                "median_prev": round(statistics.median(c["yoy3_prev"] for c in rows), 1),
                "accel": sum(1 for c in rows if c["yoy3"] >= c["yoy3_prev"]), "decel": sum(1 for c in rows if c["yoy3"] < c["yoy3_prev"])}
    tw_mid, tw_up = tw_stat("mid"), tw_stat("up")
    tw_latest = max((c["latest"] for c in twc if c.get("latest")), default=None)

    def dir_count(rows):
        c = defaultdict(int)
        for r in rows:
            c[r.get("direction", "unclear")] += 1
        return dict(c)
    dc_mid, dc_up, dc_down = dir_count(readings["mid"]), dir_count(readings["up"]), dir_count(readings["down"])

    # ---------- 信号 ----------
    S = []
    def sig(id, node, name, status, value, rule, evidence, fresh, asof, short=None):
        S.append({"id": id, "node": node, "name": name, "short": short or name.split("：")[0], "status": status, "value": value, "rule": rule, "evidence": evidence, "fresh": fresh, "asof": asof})
    pa = px.get("asof")
    # 1 股价
    st = "on" if mid_dd is not None and mid_dd <= th("dd_on", -35) else "watch" if mid_dd is not None and mid_dd <= th("dd_watch", -20) else "off"
    sig("px_dd", "price", "中游篮子较 52 周高点回撤", st, f"{mid_dd}%", f"≤{th('dd_watch', -20)}% 观察，≤{th('dd_on', -35)}% 触发",
        f"下游 {down_dd}% · 中游 {mid_dd}% · 上游 {up_dd}%（各层篮子等权均值）", "每日", pa, short="中游距高点")
    st = "on" if a200 is not None and a200 < th("breadth_on", 30) else "watch" if a200 is not None and a200 < th("breadth_watch", 50) else "off"
    sig("px_breadth", "price", "全链站上 200 日线的比例", st, f"{a200}%", f"<{th('breadth_watch', 50)}% 观察，<{th('breadth_on', 30)}% 触发",
        f"{len(chain_t)} 只样本里 {a200}% 在 200 日线上方，{a50}% 在 50 日线上方", "每日", pa)
    # 2 信用与融资
    cds = nar.get("credit", {})
    st = "on" if (neo_dd is not None and neo_dd <= th("neo_dd_on", -50)) or (hyg.get("dd") is not None and hyg["dd"] <= th("hyg_on", -5)) else \
         "watch" if (hyg.get("dd") is not None and hyg["dd"] <= th("hyg_watch", -2.5)) else "off"
    sig("credit", "credit", "信用压力（Neocloud 股价 + 高收益债 + CDS）", st, f"Neocloud {neo_dd}%",
        f"Neocloud 篮子回撤 ≤{th('neo_dd_on', -50)}% 或 HYG 回撤 ≤{th('hyg_on', -5)}% 触发",
        f"Neocloud 篮子距高点 {neo_dd}%；HYG 距高点 {hyg.get('dd')}%。" + (cds.get("text") or ""), "每日 + CDS 为快照", pa, short="信用压力：")
    st = "na" if window_ratio is None else "on" if window_ratio < th("window_on", 0.35) else "watch" if window_ratio < th("window_watch", 0.6) else "off"
    sig("window", "credit", "融资窗口：近 30 天新增债务与租赁 / 前 90 天月均", st, f"{window_ratio}×", f"<{th('window_watch', 0.6)}× 观察，<{th('window_on', 0.35)}× 触发（窗口关闭）",
        f"近 30 天债务 + 租赁新增 {zb(d30)}，前 90 天月均 {zb(d_prev90)}", "每 3 小时（账本）", g["generated_at"][:10])
    st = "na" if risky_share is None else "on" if risky_share >= th("risky_on", 65) else "watch" if risky_share >= th("risky_watch", 50) else "off"
    sig("mix", "credit", "借款人质量：非巨头借款占比（本季）", st, f"{risky_share}%", f"≥{th('risky_watch', 50)}% 观察，≥{th('risky_on', 65)}% 触发",
        f"{cur_q['q']} 债务 + 租赁里，AI 实验室 / Neocloud / 开发商 / SPV 借了 {zb(cur_q['risky'] * 1e9)}，巨头云 {zb(cur_q['ig'] * 1e9)}" if cur_q else "", "每 3 小时（账本）", g["generated_at"][:10])
    # 3 现金流
    st = "na" if cap_ratio is None else "on" if cap_ratio >= th("capex_on", 100) else "watch" if cap_ratio >= th("capex_watch", 75) else "off"
    sig("capex_ocf", "cash", "五家巨头 capex / 经营现金流", st, f"{cap_ratio}%", f"≥{th('capex_watch', 75)}% 观察，≥{th('capex_on', 100)}% 触发（合计自由现金流转负）",
        f"{agg_rows[-1]['q']} 合计 capex {zb(agg_rows[-1]['capex'] * 1e9)}，经营现金流 {zb(agg_rows[-1]['ocf'] * 1e9)}；单季自由现金流为负：{'、'.join(neg_fcf) or '无'}" if agg_rows else "", "每季（SEC XBRL）", agg_rows[-1]["q"] if agg_rows else None, short="capex ÷ 经营现金流")
    # 4 capex 指引（人工 / 调研快照）
    m = nar["manual_signals"]["capex_guide"]
    sig("capex_guide", "guide", "capex 指引：有没有人下修或说增速见顶", m["status"], m["value"], "任一巨头下修需求口径的指引即触发；因交付延迟的小幅下调记观察", m["evidence"], "财报季更新（调研快照）", nar["date"], short="指引")
    # 5 订单与撤单
    camt = usd(cancels)
    st = "on" if len(cancels) >= th("cancel_on_n", 5) or camt >= th("cancel_on_usd", 50e9) else "watch" if len(cancels) >= th("cancel_watch_n", 2) else "off"
    sig("cancel", "orders", f"撤单：近 {th('cancel_window_days', 90)} 天被取消的交易", st, f"{len(cancels)} 笔 / {zb(camt)}",
        f"≥{th('cancel_watch_n', 2)} 笔观察；≥{th('cancel_on_n', 5)} 笔或 ≥{zb(th('cancel_on_usd', 50e9))} 触发",
        "；".join(f"{e['announced'][5:]} {e['title_zh'][:26]}" for e in cancels[:4]) + (f"；另有 {len(stress_cands)} 条缩减 / 推迟类候选" if stress_cands else ""),
        "每 3 小时（账本）", g["generated_at"][:10])
    oratio = round(o90 / o_prev90, 2) if o_prev90 else None
    st = "na" if oratio is None else "on" if oratio < th("orders_on", 0.3) else "watch" if oratio < th("orders_watch", 0.6) else "off"
    sig("orders", "orders", "新签订单：近 90 天 / 前 90 天", st, f"{oratio}×", f"<{th('orders_watch', 0.6)}× 观察，<{th('orders_on', 0.3)}× 触发",
        f"近 90 天新签订单 {zb(o90)}，前 90 天 {zb(o_prev90)}（只含披露金额的）", "每 3 小时（账本）", g["generated_at"][:10])
    c90, cp90 = usd(circ90), usd(circ_prev90)
    st = "on" if c90 >= th("circ_on", 60e9) else "watch" if c90 >= th("circ_watch", 20e9) else "off"
    sig("circular", "credit", "循环融资：供应商给客户的钱（近 90 天）", st, zb(c90), f"≥{zb(th('circ_watch', 20e9))} 观察，≥{zb(th('circ_on', 60e9))} 触发",
        f"芯片 / 设备商对客户的入股、担保、租赁：累计 {zb(usd(circ))}（{len(circ)} 笔），近 90 天 {zb(c90)}，前 90 天 {zb(cp90)}", "每 3 小时（账本）", g["generated_at"][:10])
    # 6 交期与价格（调研快照）
    ease, tight = dc_mid.get("easing", 0), dc_mid.get("tightening", 0)
    st = "na" if not readings["mid"] else "on" if ease >= max(tight, 1) and ease >= 3 else "watch" if ease >= 1 else "off"
    sig("lead", "supply", "中游价格 / 交期：转松的读数有几条", st, f"{ease} 松 / {tight} 紧" if readings["mid"] else "待补",
        "出现 1 条转松记观察；转松不少于转紧且 ≥3 条触发", nar["manual_signals"]["lead"]["evidence"], "调研快照（月更）", nar["date"], short="中游读数")
    # 7 月营收
    if tw_mid:
        st = "on" if tw_mid["median_yoy3"] < 0 else "watch" if tw_mid["decel"] > tw_mid["accel"] else "off"
        sig("tw_rev", "revenue", "台湾中游月营收：3 个月合计同比", st, f"+{tw_mid['median_yoy3']}%" if tw_mid["median_yoy3"] >= 0 else f"{tw_mid['median_yoy3']}%",
            "多数样本减速记观察；中位数同比转负触发",
            f"中游 {tw_mid['n']} 家中位数 {tw_mid['median_yoy3']}%（前 3 个月 {tw_mid['median_prev']}%），{tw_mid['accel']} 家加速、{tw_mid['decel']} 家减速；"
            f"上游 {tw_up['n']} 家中位数 {tw_up['median_yoy3']}%，{tw_up['accel']} 加速、{tw_up['decel']} 减速" if tw_up else "", "每月 10 日前后", tw_latest, short="中游 3 个月同比")

    nodes = [("price", "股价", "每日"), ("credit", "信用与融资", "每 3 小时"), ("cash", "现金流", "每季"), ("guide", "capex 指引", "每季"),
             ("orders", "订单与撤单", "每 3 小时"), ("supply", "交期与价格", "月度快照"), ("revenue", "出货 / 月营收", "每月")]
    chain = []
    for nid, name, cad in nodes:
        ss = [s for s in S if s["node"] == nid]
        chain.append({"id": nid, "name": name, "cadence": cad, "status": worst(*[s["status"] for s in ss]) if ss else "na",
                      "headline": "；".join(f"{s['short']} {s['value']}" for s in sorted(ss, key=lambda s: -{"on": 2, "watch": 1}.get(s["status"], 0))[:1]), "signals": [s["id"] for s in ss]})
    front = max((i for i, c in enumerate(chain) if c["status"] == "on"), default=-1)

    def layer(L, key):
        return {"id": L, **nar["layers"][L], "baskets": lay(L), "readings": readings[key], "dir": dir_count(readings[key]),
                "tw": [c for c in twc if c["layer"] == L]}

    doc = {
        "generated_at": now_z(), "ledger_at": g["generated_at"], "prices_asof": px.get("asof"), "prices_at": px.get("generated_at"),
        "tw_latest": tw_latest, "sec_q": agg_rows[-1]["q"] if agg_rows else None, "narrative_date": nar["date"],
        "site_url": yaml.safe_load((ROOT / "config" / "settings.yaml").read_text()).get("site_url"),
        "verdict": nar["verdict"], "chain": chain, "front": front, "signals": S,
        "counts": {k: sum(1 for s in S if s["status"] == k) for k in ("on", "watch", "off", "na")},
        "layers": [layer("down", "down"), layer("mid", "mid"), layer("up", "up")],
        "funding": {"monthly": monthly_rows, "debt_q": debt_q, "risky_share": risky_share, "window_ratio": window_ratio,
                    "circ": {"total": usd(circ), "n": len(circ), "d90": c90, "prev90": cp90, "items": [slim(e) for e in sorted(circ, key=lambda e: -(e["amount_usd"] or 0))[:8]]},
                    "buyers": top_buyers, "lab_share": lab_share, "order_book": tot_orders,
                    "cancels": [slim(e) for e in cancels], "stress_cands": [slim(c) for c in stress_cands[:6]], "recent": [slim(e) for e in recent],
                    "capex": capex_cos, "agg": agg_rows, "neg_fcf": neg_fcf,
                    "totals": {k: {"usd": v["usd"], "count": v["count"]} for k, v in g["stats"]["by_kind"].items() if v["count"]}},
        "market": {"baskets": px.get("baskets", []), "tickers": px.get("tickers", {}), "names": px.get("names", {}),
                   "rotation_m3": rot, "a200": a200, "a50": a50, "dd": {"down": down_dd, "mid": mid_dd, "up": up_dd}},
        "tracking": nar["tracking"], "checklist": nar.get("checklist", []), "changes": nar.get("changes", []),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    (OUT / "cycle.json").write_text(blob)
    tpl = (CY / "template.html").read_text() if (CY / "template.html").exists() else None
    if tpl:
        safe = blob.replace("</", "<\\/")
        (OUT / "artifact.html").write_text(tpl.replace("/*__DATA__*/null", safe))
        full = ('<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="UTF-8">\n<meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">\n'
                '<meta name="description" content="AI 供应链压力走到了哪一环：股价、信用、现金流、capex 指引、订单、价格、月营收七环传导链，接 AI 订单与债务账本，自动更新。">\n'
                + tpl.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "").replace("</style>\n<!--BODY-->", "</style>\n</head>\n<body>", 1) + "\n</body>\n</html>\n")
        site = ROOT / "docs"
        if site.exists():
            (site / "data").mkdir(exist_ok=True)
            (site / "cycle.html").write_text(full)
            (site / "data" / "cycle.json").write_text(blob)
    print("cycle.json", len(blob) // 1024, "KB | 信号", doc["counts"], "| 压力前沿", chain[front]["name"] if front >= 0 else "无")
    for s in S:
        print(f"  [{s['status']:5}] {s['node']:8} {s['name']}：{s['value']}")
    return doc


if __name__ == "__main__":
    build()
