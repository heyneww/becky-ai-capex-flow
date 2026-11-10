"""两站交叉对账：我们的 graph.json vs GL-AI-live 的 graph.json，按交易类型逐笔匹配。
用法：python3 tools/recon/xmatch_gl.py [-v]        # 自动下载两站最新 graph.json 到 tools/recon/cache/
      python3 tools/recon/xmatch_gl.py ours.json gl.json [-v]
输出：tools/recon/cache/xmatch.json（每类 both / diff / gl_only / xkind / ours_only），-v 逐行打印。
实体对照：MAN 手工字典 + ticker / 名称匹配，结果写 tools/recon/gl_entity_map.json。2026-11-09 首版（原在 scratchpad，已被系统清空一次，故进仓库）。"""
import json, re, collections, itertools, sys, pathlib, urllib.request
from datetime import date
HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / "cache"; CACHE.mkdir(exist_ok=True)
S = str(CACHE) + "/"
URLS = {"ours": "https://heyneww.github.io/becky-ai-capex-flow/data/graph.json", "gl": "https://heyneww.github.io/GL-AI-live/data/graph.json"}
args = [a for a in sys.argv[1:] if not a.startswith("-")]
if len(args) >= 2:
    A = json.load(open(args[0])); B = json.load(open(args[1]))
else:
    for k, u in URLS.items():
        with urllib.request.urlopen(u, timeout=60) as r:
            (CACHE / f"{k}.json").write_bytes(r.read())
    A = json.load(open(CACHE / "ours.json")); B = json.load(open(CACHE / "gl.json"))
AE={e["id"]:e for e in A["entities"]}; BE={e["id"]:e for e in B["entities"]}
MAN={"tesla":"TSLA","us-government":"US_GOV","humain":"HUMAIN","minimax":"MINIMAX","scale-ai":"SCALE_AI","io-products":"IO_PRODUCTS","disney":"DIS","chirisa-technology-parks":"CHIRISA","powerhouse-data-centers":"POWERHOUSE","moonshot-ai":"MOONSHOT","safe-superintelligence":"SSI","groq":"GROQ","intel":"INTC","samsung-electronics":"SAMSUNG","airtrunk":"AIRTRUNK","alibaba":"BABA","alphabet":"GOOGL","amazon":"AMZN","amd":"AMD","anthropic":"ANTHROPIC","apollo":"APO","apollo-spacex-gpu-lessor":"SPACEX","apple":"AAPL","applied-digital":"APLD","baidu":"BIDU","bank-lenders":"LENDER_SYNDICATE","bank-of-america":"BAC","beignet-investor":"BEIGNET","blackstone":"BX","blue-owl":"OWL","bond-investors":"BONDHOLDERS","broadcom":"AVGO","broadcom-ai-xpv":"ANTHROPIC_XPU_SPV","broadcom-anthropic-rack-spv":"ANTHROPIC_XPU_SPV","brookfield":"BAM","bytedance":"BYTEDANCE","cerebras":"CEREBRAS","cipher-mining":"CIFR","citigroup":"C","core-scientific":"CORZ","coreweave":"CRWV","crusoe":"CRUSOE","crux-ai":"CRUX_AI","deutsche-bank":"DB","ecodatacenter":"ECODATACENTER","firmus":"FIREBIRD","galaxy-digital":"GLXY","goldman-sachs":"GS","hut-8":"HUT","iren":"IREN","jpmorgan":"JPM","lambda":"LAMBDA","magnetar":"MAGNETAR","meta":"META","micron":"MU","mistral":"MISTRAL","morgan-stanley":"MS","mufg":"MUFG","nebius":"NBIS","nvidia":"NVDA","openai":"OPENAI","oracle":"ORCL","pimco":"PIMCO","related-digital":"RELATED","riot-platforms":"RIOT","sk-hynix":"SKHYNIX","smbc":"SMBC","softbank":"SOFTBANK","sopaipilla-investor":"SOPAIPILLA","spacex":"SPACEX","stack-infrastructure":"STACK","tencent":"TCEHY","terawulf":"WULF","tsmc":"TSM","valor-compute-infrastructure":"VALOR_COMPUTE","vantage-data-centers":"VANTAGE","xai":"SPACEX","zankore":"NEW_ZANKORE","nexus-data-centers":"NEXUS","eagle-point":"PRIVATE_CREDIT","microsoft":"MSFT","public-equity-investors":"PUBLIC_INVESTORS","private-investors":"PRIVATE_ROUND","private-credit":"PRIVATE_CREDIT","undisclosed":"UNDISCLOSED"}
def norm(s): return re.sub(r"[^a-z0-9]+"," ",(s or "").lower()).strip()
by_ticker={e["ticker"].upper():e["id"] for e in A["entities"] if e.get("ticker")}
by_name={norm(e["name"]):e["id"] for e in A["entities"]}
for e in A["entities"]:
    for a in (e.get("aliases") or []): by_name.setdefault(norm(a),e["id"])
MAP={}
for b in B["entities"]:
    i=b["id"]
    if i in MAN: MAP[i]=MAN[i]; continue
    t=(b.get("ticker") or "").upper().split(":")[-1]
    if t and t in by_ticker: MAP[i]=by_ticker[t]; continue
    n=norm(b["name"].split("(")[0].split("/")[0])
    if n in by_name: MAP[i]=by_name[n]; continue
    first=n.split(" ")[0] if n else ""
    cands=[k for k in by_name if k==first or k.startswith(first+" ")] if len(first)>3 else []
    if len(set(by_name[k] for k in cands))==1: MAP[i]=by_name[cands[0]]
used=collections.Counter(x for e in B["edges"] for x in (e["from"],e["to"]))
unm=[(i,BE[i]["name"],used[i]) for i in used if i not in MAP]
print("unmapped GL entities used in edges:",sorted(unm,key=lambda x:-x[2]))
json.dump(MAP,open(HERE/"gl_entity_map.json","w"),indent=0)
def d(s):
    try: return date.fromisoformat(s[:10])
    except: return None
def close(a,b,t): return a and b and abs(a-b)/max(a,b)<=t
oursE=[e for e in A["edges"] if not e.get("cancelled")]
res={}
for kind in ("order","investment","debt","lease","guarantee","vendor_financing"):
    gl=[e for e in B["edges"] if e["kind"]==kind and e.get("status")!="cancelled"]
    ours=[e for e in oursE if e["kind"]==kind]
    used=set(); rows=[]
    for e in gl:
        f,t=MAP.get(e["from"]),MAP.get(e["to"])
        pool=[o for o in ours if o["id"] not in used and ({o["from_entity"],o["to_entity"]}=={f,t} or (f in (o["from_entity"],o["to_entity"]) and t in (o["from_entity"],o["to_entity"])))]
        # relax: same pair any kind (cross-kind) later
        best=None
        for o in pool:
            da,db=d(e.get("announced") or ""),d(o.get("announced") or ""); dd=abs((da-db).days) if da and db else 999
            if (not e.get("amount_usd") and not o.get("amount_usd")) or close(e.get("amount_usd"),o.get("amount_usd"),0.15):
                if dd<=400 and (best is None or dd<best[0]): best=(dd,o)
        if not best and pool:  # same pair, amount differs
            o=min(pool,key=lambda o:abs(((d(e.get("announced") or "") or date(2000,1,1))-(d(o.get("announced") or "") or date(2000,1,1))).days)); best=("diff",o)
        if best:
            used.add(best[1]["id"]); rows.append(("both" if best[0]!="diff" else "diff",e,best[1]))
        else:
            # cross-kind: same pair in any kind
            xk=[o for o in oursE if o["kind"]!=kind and {o["from_entity"],o["to_entity"]}=={f,t} and close(e.get("amount_usd"),o.get("amount_usd"),0.15)]
            rows.append(("xkind" if xk else "gl_only",e,xk[0] if xk else None))
    ours_only=[o for o in ours if o["id"] not in used]
    res[kind]=dict(rows=rows,ours_only=ours_only)
    c=collections.Counter(r[0] for r in rows)
    print(f"== {kind}: GL {len(gl)} / ${sum(e.get('amount_usd') or 0 for e in gl)/1e9:.0f}B ; ours {len(ours)} / ${sum(o.get('amount_usd') or 0 for o in ours)/1e9:.0f}B ; match {dict(c)} ; GL-only $ {sum(r[1].get('amount_usd') or 0 for r in rows if r[0]=='gl_only')/1e9:.0f}B ; ours-only {len(ours_only)} / ${sum(o.get('amount_usd') or 0 for o in ours_only)/1e9:.0f}B ; xkind $ {sum(r[1].get('amount_usd') or 0 for r in rows if r[0]=='xkind')/1e9:.0f}B")
    if "-v" in sys.argv:
        for r in sorted([r for r in rows if r[0] in ("gl_only","xkind","diff")],key=lambda r:-(r[1].get("amount_usd") or 0))[:25]:
            o=r[2]; print(f"   {r[0]:7} {r[1]['from']}→{r[1]['to']} {(r[1].get('amount_usd') or 0)/1e9:.1f} {r[1].get('announced')} {r[1]['title_en'][:60]}" + (f"  || ours #{o['id']} {o['kind']} {(o.get('amount_usd') or 0)/1e9:.1f} {o.get('announced')}" if o else ""))
        print("   -- ours only:",[(o["id"],o["from_entity"],o["to_entity"],round((o.get("amount_usd") or 0)/1e9,1),o.get("announced")) for o in sorted(ours_only,key=lambda o:-(o.get("amount_usd") or 0))[:15]])
json.dump({k:{"rows":[(r[0],r[1]["id"],r[2]["id"] if r[2] else None) for r in v["rows"]],"ours_only":[o["id"] for o in v["ours_only"]]} for k,v in res.items()},open(S+"xmatch.json","w"))
