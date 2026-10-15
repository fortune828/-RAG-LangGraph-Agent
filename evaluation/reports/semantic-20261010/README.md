# 实验档案与复现 · semantic-20261010

结果先读 [主报告](../../../docs/archive/experiments/semantic-optimization-20261010.md)。本目录只有合成开发输入、隔离确认输入与模型返回；不包含生产聊天、密钥或模型内部思考正文。全部实际模型请求进入同一个累计 `ledger.json`，上限 90 次 / 700,000 tokens，实际 78 次 / 410,996 tokens。

## 记录索引

| 路径 | 实验单位与目的 | 真实运行边界 |
|---|---|---|
| `context/` | 4 个旧开发输入 × baseline/roles/grouped/core | 各一次生成、不修复；真实角色、分组、核心原典均未部署 |
| `hybrid/` | 4 题 × 基线/回答或工具候选/C | 完整图、至多一次修复；答复与工具合一候选已撤销，源码仍保存 |
| `rag/` | 2 个生活文义题 × memory/classic/full | 同模型单次生成、不修复；memory 无引文权限仅用于离线消融 |
| `static/` | 一个责任更正输入 × 旧词面爻注/实际相关爻注 | 同生成格式、模型、历史，只有证据选择不同 |
| `recovery/` | 2 个已有 length 响应，各一次修复复放 | 不新增独立题；不改原失败 |
| `rag-recovery/` | full 组 2 个失败各一次修复 | 不覆盖原 RAG 0/2，不重算为首次完成 |
| `final/` | 4 个隔离输入 × v13/v14 | 冻结后才读题，各组一次；不是 v15/v16 数据 |
| `release/` | v15 纯文献及混合新题各一次 | 2 条机械完成，但混合新题未 handoff，业务失败 |
| `continuous/` | v15 的真实连续三轮 | 同一卦，实际上一轮答复进入后续历史；一个会话组 |
| `stress/` | v15 同一模糊出处输入 C/D | 按 C 词面检索失败选择的诊断题；原 D 为校验失败 |
| `fixtures/` | 输入、AI 核对的文义参考、隔离题及压力题选题记录 | 标签仅供评分，不进入 Prompt；不是专家 Gold |
| `blind/` | 匿名材料、独立 AI 评分和理由 | AI 非真人；未完成与被拒候选分别记录 |

每个阶段有 `inputs.json`、`rows.json`、`answers.md`、`summary.json`、`manifest.json`、源码快照和逐单元 `*.jsonl`。完整引文/原文、初始与最终证据、工具请求、实际执行状态在 JSON/JSONL，Markdown 用于直接阅读正文和历史。修复前候选在 `validation` 事件；截断/异常响应在 `model_result`。单元的 `completed` 只表示该次机械检查完成，不代表有用、来源任务完成或遵循新聊天规则。

实际参数以每个 `model_request.payload` 为准；记录实际返回 model、usage、错误与 finish reason。未发送 seed/temperature，不虚构默认值。reasoning 只留长度/哈希。四题隔离的 `decision.json` 含运行前生产代码哈希、输入哈希及选择依据；最终工程代码另由验证文件记录，不能倒称为当时冻结版本。

## 离线复算，无需 API

在仓库根目录运行：

```bash
.venv/bin/python -m evaluation.semantic_reports summarize
.venv/bin/python -m pytest -q tests_python
npm test
npm run lint
npm run build
```

复算只读原始记录，输出 `tables.csv`、`usage.json`、`totals.json`、`review-revealed.json`；不调用模型、不改变旧评分。按 stage/arm/config/graph 分组。ledger 比较、未知用量、未结束请求、工具请求/预算尝试/成功/空结果/拒绝分开统计。原本 stage 误判和 manifest 文案错误见 `errata.json`；派生报表使用 effectiveStage，不修改原请求。

费用用 `../autonomous-20261010/capabilities/price_table.json` 的 2026-10-10 官方来源快照，按 UTC 周末价格和实际缓存/输出用量估算。价格文件哈希写入 totals；其他日期不套该价，返回未知。不是账单核对，包含失败请求；repair 诊断只计新增那一次请求，原主调用已在原阶段计费。

## 新的真实开发回放

现有目录拒绝覆盖。先配置本地 `.env.local`（不得提交密钥），再指定不存在的输出根：

```bash
.venv/bin/python -m evaluation.semantic release --output-root /tmp/iching-semantic-release-new
.venv/bin/python -m evaluation.semantic continuous --output-root /tmp/iching-semantic-continuous-new
.venv/bin/python -m evaluation.semantic stress --output-root /tmp/iching-semantic-stress-new
.venv/bin/python -m evaluation.semantic_reports summarize --base /tmp/iching-semantic-release-new
```

这些命令会付费，使用当前代码；不复现旧版模型输出的字节。每个全新根有自己的 90 次 / 700,000 tokens 上限；本轮原实验始终共享本目录同一个 ledger，没有用新根重置累计消耗。新时间运行的价格估计默认未知，应另冻结当日价目。

重放 `context` / `rag` / `static` 也支持全新根，但当前 prepare 与旧阶段可能不同，须记录为新实验。重现历史代码条件，应在独立 Git worktree 中检出基线，再覆盖对应阶段 `source/backend/` 快照及记录的执行器；保留其 requirements/依赖环境。`hybrid` 的被拒候选从档案快照加载，仅在评测脚本存在，生产没有策略壳。旧基线 graph/knowledge/validation 从 `f277be7` 加载。

`final` 会校验 `decision.json` 和所有生产文件哈希，当前代码故意不能直接运行旧冻结四题。不要通过删掉哈希检查或重写 decision 来复用“未见集”称呼；这些题已经揭示。若重现旧 v14，应在独立 worktree 恢复该次源码及 decision，并使用新记录目录；再跑得到的是复测，仍非新留出。

`recovery` 和 `rag-recovery` 读取本档案相应原失败请求，仅发一次 disabled / 8192 修复请求。它们不是重新跑完整会话，结果不得与首次成功率合并。

## AI 评阅及事实边界

匿名材料提供问题、程序卦象、必要历史、原文和回答，隐藏策略、模型配置、工具轨迹及实现源码。主优化者在评分结束后才读取解盲键。四维各 0–2：`userFacts`、`classicalMeaning`、`analogyFit`、`dialogueHelp`；文献题无现代应用时 analogy 为 N/A，无答案不填语义 0。可读但被拒的候选单列 candidateOnly，不能计入已交付均分。没有真人评分或双评审一致性。

`blind/stress/materials.json` 特别保留 D 最后被拒候选供语义核对，并另列真实可见 fallback；原 `rows.json` 保持真实交付失败。该包 JSON 是权威材料。`knowledge-review-key.json` 对应跨 RAG/static 的早期匿名包，其余阶段 key 在各阶段目录。解盲结果保留原分数和理由，不因后续修复改分。

标签泄漏检查覆盖评测专用字段名和特异长文本片段；通过不等于概念泄漏的数学证明。压力题完整保留两个候选，第一个因“哪一爻”被误解析而排除，第二个因真实 C 词面缺口被选择。这种选择偏差有意用于失败诊断，不能当作 Agent 优胜比例。

## 工程与审查

- [引文差异及旧报告误归因](validation-audit.md)：438 条旧引文，两个不匹配来自同一案例的获/獲差异。
- [路由边界审查](audit-review.md)：混合新题、未知出处及多卦多爻交叉解析问题。
- [最终代码复核](final-code-review.md)：修复预算、权限、引文与持久化；不代替真实语义验收。
- [工程验证与最终源码](verification.json)：Python/TypeScript/lint/build、隐私检查与版本；历史失败保留。

正式专家 Gold、40+40、检索 Recall、真人交流验收均未在本轮完成。没有把简单的有出处、可解析或 AI 分数提高当作系统总体成功。
