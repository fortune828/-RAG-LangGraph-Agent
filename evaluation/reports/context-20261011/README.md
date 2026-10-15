# 本轮材料索引与复现

结论见[修复报告](../../../docs/reports/context-repair-20261011.md)。最终生产graph v18 / prompt v16；原始阶段版本以manifest和实际源码快照为准。

## 阅读与评分

- [最终v18午饭/理发六轮](human-random/materials.html)：新开发冒烟；[空白人工CSV](human-random/scores.csv)、[评分说明](human-random/README.md)。
- [v17六段独立出题对话18轮](human-random-v17/materials.html)：在发现失败历史bug前运行，不能标成v18；[空白CSV](human-random-v17/scores.csv)。
- [原14份人工评分整理](human-baseline/README.md)：源文件字节未改，原CTX标签未采集而非零错误。
- 每个阅读包都有Markdown和JSON版。AI评语单列 `ai-review.json/md`，人工评分前不要阅读；knowledge-review CSV和机器检查在阅读包外，体验评分不要求逐字校经。

## 实验记录

| 目录/文件 | 内容 |
|---|---|
| baseline-source | 最初backend完整快照，旧graph v16/validator用于机制回放 |
| baseline | 两段真实随机API连续对话，共6轮 |
| probe | 4次实际追问 × 原生角色/资料前置两个候选 |
| diagnostic | 同4次追问 × 首段任务/首轮证据两个候选 |
| history-probe | 同4次追问 × 仅用户历史/关闭Thinking两个诊断 |
| blind-probe、blind-diagnostic、blind-history | 匿名输出、独立AI评分和理由；人类CSV未填 |
| knowledge | graph v17两条混合原典/注解问题的真实API回归 |
| confirmation | graph v17六段独立AI出题真实会话，共18轮；包括失败 |
| postfix | graph v18两段真实随机API开发冒烟，共6轮 |
| release-decision.json、postfix-decision.json | 两次运行前版本/配置与源码哈希冻结，不跨版本重新贴标签 |
| failed-history-diagnostic.json | 真实失败轮的旧请求缺失事实与v18历史重建对照，不是新增模型调用 |
| plan.json、ledger.json | 初始有界设计、价格来源、共享调用/token累计账本 |
| summary.json、tables.md、usage.json、ai-quality.json | 从原始结果离线重算；原始usage未知字段保留，评分NA不填0 |
| verification.json、verification/ | 测试范围、已运行命令、Python输出、原用户评分与最终冻结哈希核对 |

每个运行目录有 `manifest.json`、`scenarios.json`、`source/`、`rows.json`，以及逐轮JSONL和新建/最终会话快照。JSONL保存模型可见请求、响应、工具和校验/修复；内部思考内容不公开。全为合成场景，不含生产聊天。

## 离线复算（无API费用）

在仓库根目录执行：

```bash
.venv/bin/python -m evaluation.context_results
.venv/bin/python -m pytest -q
```

汇总使用各轮真实记录及冻结价格，区分端到端和单次生成回放。P95仅为小样本描述值；机械完成不代表用户目标完成。评分按匿名key映射，人工与AI分别保留。

原评分整理和新阅读包可导出到一个尚不存在的目录：

```bash
.venv/bin/python -m evaluation.context_review normalize evaluation/reports/formal-20261010/human/first-batch-20261011/scores.csv /tmp/iching-original-rating-copy
.venv/bin/python -m evaluation.context_review pack evaluation/reports/context-20261011/postfix/rows.json /tmp/iching-dialogue-review
```

工具拒绝覆盖已有包。人工CSV不会从AI评分自动回填。某次人工CSV实际是Markdown表，导出器已兼容，源文件保持原样。

## 重新运行真实随机流程（会调用付费API）

以下明确另建输出目录，保留当前生产版本和本轮原档案。每次复跑会产生新的真实随机卦象，不能保证与旧结果逐字相同。

```bash
.venv/bin/python - <<'PY'
import asyncio
from pathlib import Path
from evaluation import context_experiment as run

run.BASE = Path('/tmp/iching-new-random-run')
run.BASE.mkdir(exist_ok=False)
run.MAX_CALLS, run.MAX_TOKENS = 20, 200000
asyncio.run(run.conversations('postfix', run.POSTFIX))
PY
```

密钥使用现有环境/`.env.local`，不要写入命令或记录。脚本通过正常新建会话和追问API，一次抽取、不换卦、不筛除失败。模型配置在脚本和每个manifest中明示，所有请求前经过共享预算检查。不要复用旧ledger冒称新一次独立预算。

要重跑本轮隔离机制对照，可在新目录复制原 `baseline/` 和 `baseline-source/`，调用 `run.probe('probe')`、`run.probe('diagnostic')` 或 `run.probe('history-probe')`。这复用已发生的真实随机输入，属于生成请求回放，不属于新起卦。另设新预算/目录，保留新原始输出；不要覆盖原记录。若要重跑历史v17整图，须使用该阶段 `source/backend/` 快照和同版本依赖隔离执行，不能把当前v18运行标作v17。

本轮依赖沿用仓库锁定环境，主要工程检查见verification；API别名、服务负载和采样会变化。可复现指输入、请求选项、实现和统计可追溯，不等于供应商必定返回完全相同文本。
