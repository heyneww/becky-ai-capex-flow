# 债务对账底稿（vs helajo）

- `debt-reconciliation.html`：发布到 https://claude.ai/artifact/JmejtudktrSinEohfFbZX1 的页面源文件（中英双语，数据内嵌在 `const D = {...}`）。
- 对账方法（v3，2026-10-29）：拉 helajo 的 `data/graph.json`，取 kind=debt；按借款方映射到我们的实体 id，金额 ±12%、日期 120 天内匹配我们的计入边，允许 2–3 个分币种档合并匹配；手工字典 PIN（强制配对）、SUB（他们拆层 / 我们整笔）、GAPNOTE（逐行中英备注）。类别：both / both_diff / sub / cand / gap / scope / repaid / refi；我们独有分 extra / scope_theirs / facility / check。
- 脚本原在会话 scratchpad，已随临时目录清除；重跑时按上述方法重写，把脚本放到本目录。

## 两站交叉对账 `xmatch_gl.py`（2026-11-09）

```bash
python3 tools/recon/xmatch_gl.py -v        # 自动下载两站 graph.json 到 tools/recon/cache/（不进 git）
```
按交易类型把 GL-AI-live（公司镜像 `heyneww.github.io/GL-AI-live`）的每笔与我们的匹配：同双方（任一方向）金额 ±20% 为 both / diff，
同双方但类型不同为 xkind（多是「数据中心容量租约」他们记租赁我们记订单，已于 11-09 统一记租赁），其余 gl_only / ours_only。
实体对照表 `gl_entity_map.json`：GL 的 slug → 我们的 id，MAN 字典里手工维护别名，其余按 ticker / 名称匹配。

### 2026-11-09 口径对齐后的差异结论（供下次对账参考）
- 定义差：担保 GL 不进总额（我们已改为单列）；年度采购额（Apple→TSMC、NVIDIA→TSMC）GL 当交易，我们不收；
  Anthropic S-1 承诺表（谷歌 / 亚马逊千亿级）我们收，GL 记上限或不收；ATM 计划我们标「额度」。
- 覆盖差：GL 多 2023–2025 的订单 / 投资历史（约 72 笔订单 $214B、91 笔投资 $376B），我们的债务按 EDGAR 逐次定价更细。
- 质量差：我们的媒体来源里 Yahoo / BigGo / TradingView 等转载站占比高，11-09 起不再算独立媒体（`config/publishers.yaml`）；传闻状态不再升级。
