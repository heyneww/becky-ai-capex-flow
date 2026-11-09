# 债务对账底稿（vs helajo）

- `debt-reconciliation.html`：发布到 https://claude.ai/artifact/JmejtudktrSinEohfFbZX1 的页面源文件（中英双语，数据内嵌在 `const D = {...}`）。
- 对账方法（v3，2026-10-29）：拉 helajo 的 `data/graph.json`，取 kind=debt；按借款方映射到我们的实体 id，金额 ±12%、日期 120 天内匹配我们的计入边，允许 2–3 个分币种档合并匹配；手工字典 PIN（强制配对）、SUB（他们拆层 / 我们整笔）、GAPNOTE（逐行中英备注）。类别：both / both_diff / sub / cand / gap / scope / repaid / refi；我们独有分 extra / scope_theirs / facility / check。
- 脚本原在会话 scratchpad，已随临时目录清除；重跑时按上述方法重写，把脚本放到本目录。
