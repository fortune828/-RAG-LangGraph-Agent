# 自主实验索引 · 2026-10-10

先读 [结果报告](../../../docs/archive/experiments/autonomous-optimization-20261010.md)。全部为真实 API + 合成输入；`fixtures` 的标签是 AI 拟定，不是专家 Gold。旧阶段及本轮失败均保留。

| 目录 | 内容 | 控制/限制 |
|---|---|---|
| [capabilities](capabilities/report.md) | 账号模型列表、4次小调用、官方能力/价格 | 仅证明可调用，非质量测评 |
| [screening](screening/answers.md) | 4题 × 4配置 | 同 Prompt/卦/证据/历史，一次生成，无修复 |
| [finalists](finalists/answers.md) | 同4题，Flash off复跑及Pro low | 8份全部保留；不挑选更好的一次 |
| [rag](rag/answers.md) | 3题 × memory/classic/full | memory明确允许模型知识，引用指标N/A；午饭classic/full证据相同 |
| [agent](agent/answers.md) | 3查证题 × B/C/D，v10 | 规则可检索；两题可得、一题确实缺失；独立于核心交流分 |
| [routing](routing/answers.md) | 同3题 × v10/v11，均Flash off | 仅图的路由投影与停止判定改变；生成Prompt/权限/校验不变 |
| [holdout](holdout/answers.md) | 6题 × 冻结非思考候选/旧high | 根优化者冻结后解封；只有小型AI拟定留出，不是正式测试集 |
| [continuous](continuous/answers.md) | 非思考候选真实连续3轮 | 后轮使用实际前轮回答，保持同卦；不是固定历史的配对结论 |
| [history-diagnostic](history-diagnostic/answers.md) | 第三轮保留/去除assistant历史 | 各1次诊断，没有据此删除生产助手历史 |
| [release-verification](release-verification/answers.md) | 暂保留 high + v11 的2条开发验证 | 含 repair 思考耗尽，不混进留出 |
| [repair-check](repair-check/answers.md)、[repair-located](repair-located/answers.md)、[repair-explained](repair-explained/answers.md) | 同一失败的非思考、位置、可读原因复放 | 前两次失败；最后机械通过仍有错误爻名 |
| [final-verification](final-verification/answers.md)、[low-verification](low-verification/answers.md) | v12 上同3题 high/low 开发复测 | high 1/3、low 3/3；高档两次空正文 |
| [confirmation](confirmation/answers.md) | 新4题 low/high 最终配置确认 | 候选冻结后首次运行；两组均3/4，不再调优 |
| [blind](blind/) | 首批57份 + 开发6份 + 最后8份独立AI匿名评阅 | 无答案N/A，核验失败提示单列任务失败；不是用户或专家验收 |

`decision.json` 冻结非思考候选；`release-decision.json` 记录暂保留 high 的决定；`confirmation-decision.json` 记录 high 再次失败后、打开新4题前冻结 low；`final-decision.json` 是最终取舍。不同阶段不得混成一次胜率。`fixtures/*freeze.json` 固定数据哈希；语料为 `knowledge-v3-20260926-r1`，所有提示词沿用 v16，图 v10/v11/v12 分开记录；最终 v13 收紧宽泛目标边界，只做工程回归，不能重标 v12 的真实确认结果。

逐目录 `inputs.json`、`manifest.json`、`source/`、每题 `.jsonl`、`rows.json`、`summary.json` 可复核。模型每次请求/可见响应、修复前候选、错误、实际usage均保留；模型内部reasoning只保留长度/哈希。工具请求、预算消耗、ok/empty/rejected分别记录。完整证据只取固定本地发布集。无生产聊天、密钥或外部 tracing。

`tables.csv/json`、`usage.json`、`totals.json`、`review-revealed.json`、`review-averages.json` 为可重算产物。费用按官方当日周六非高峰价格和实际cache hit/miss/completion估算，**不是账户账单核对**。各阶段缓存命中不同，Token下降不等于同幅度费用下降。没有收到reasoning细项的非思考请求保留null，不补0。

离线复算（无API调用）：

```bash
.venv/bin/python -m evaluation.autonomous_report
```

单阶段复算：`.venv/bin/python -m evaluation.autonomous summarize --name screening`。盲评包可由 `evaluation.autonomous_review --export <目录名>` 导出，但已揭组后不能假装重新盲评。

付费复跑可在**独立工作树**使用以下命令，先保留旧目录和ledger，输出名称使用新名称，不能覆盖现有结果：

```bash
.venv/bin/python -m evaluation.autonomous screen --name screening-rerun
.venv/bin/python -m evaluation.autonomous rag --config flash-off --name rag-rerun
.venv/bin/python -m evaluation.autonomous agent --config flash-off --name agent-rerun
.venv/bin/python -m evaluation.autonomous routing --config flash-off --name routing-rerun
.venv/bin/python -m evaluation.autonomous holdout --config flash-off --name holdout-rerun
```

上述runner使用**检出源码**，重现某历史阶段必须使用该阶段source快照/相应Git，尤其 agent 原始对照是 v10，不能用当前 v13 重跑后仍标v10。routing/holdout 的旧图由 `b4c73d4` 取出。holdout重跑已经是已见案例，不再算新留出。当前共享 ledger 累计124次主模型请求：首轮90之外，为已暴露的发布失败追加有界诊断和确认，最终上限145次、900,000 tokens。**不得清零原 ledger 假装原轮继续**。需要付费复跑时，在独立工作树把整个 `evaluation/reports/autonomous-20261010` 移到该工作树外保存，再在原路径只复制 `fixtures/`、`capabilities/`、`decision.json`、`confirmation-decision.json`；不复制 ledger。这样是独立新轮，保留旧档案。随机采样和供应商别名漂移仍使输出不可逐字确定重现。

最终配置复跑：`.venv/bin/python -m evaluation.autonomous final --config flash-low --name new-low-check`。四题配置对照：`.venv/bin/python -m evaluation.autonomous confirmation --config flash-low --name new-confirmation`。它们显式启用非思考 repair；默认 `configured_chat(config)` 仍为全阶段同配置。重跑已见四题不是新留出。
