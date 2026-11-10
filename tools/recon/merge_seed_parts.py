"""把 seeds/_part_*.yaml（子代理逐笔核实的分片）合并成一个正式种子文件，然后删除分片。
用法：python3 tools/recon/merge_seed_parts.py seeds/backfill_orders_investments_2023-2025.yaml
已存在的目标文件会被保留并追加（按 kind|from|to|announced 去重）。分片里的 new_entities 需先手工加进 config/entities.yaml。"""
import sys, glob, pathlib, yaml
ROOT = pathlib.Path(__file__).resolve().parents[2]
out = ROOT / sys.argv[1]
HEADER = ("# 订单 / 投资历史回填种子（2023–2025，2026-11-10）：线索取自 GL-AI-live 独有条目，按本站来源标准逐笔重新核实。\n"
          "# 每笔至少一个一手来源（SEC / 交易所文件 tier 1、公司新闻稿 tier 2），否则两家独立原发媒体（tier 3）；\n"
          "# 仅媒体报道、当事方未确认的记 rumored（留候选区）。verify_note 记录核实时的保留意见。\n")
key = lambda d: f"{d['kind']}|{d['from']}|{d['to']}|{d.get('announced')}"
deals = (yaml.safe_load(out.read_text(encoding="utf-8")) or {}).get("deals", []) if out.exists() else []
seen = {key(d) for d in deals}
ids = {e["id"] for e in yaml.safe_load((ROOT / "config/entities.yaml").read_text(encoding="utf-8"))["entities"]}
parts = sorted(glob.glob(str(ROOT / "seeds" / "_part_*.yaml")))
for fp in parts:
    data = yaml.safe_load(pathlib.Path(fp).read_text(encoding="utf-8")) or {}
    for s in data.get("skipped") or []:
        print("skipped by verifier:", s)
    for d in data.get("deals") or []:
        miss = [x for x in (d["from"], d["to"]) if x not in ids]
        if miss:
            sys.exit(f"实体未登记 {miss}: {d.get('title_en')}")
        if not d.get("sources"):
            sys.exit(f"无来源: {d.get('title_en')}")
        if key(d) in seen:
            print("dup, skip:", key(d)); continue
        seen.add(key(d)); deals.append(d)
out.write_text(HEADER + yaml.safe_dump({"deals": deals}, allow_unicode=True, sort_keys=False, width=1000), encoding="utf-8")
for fp in parts:
    pathlib.Path(fp).unlink()
print(f"{out.name}: {len(deals)} deals; merged {len(parts)} part files")
