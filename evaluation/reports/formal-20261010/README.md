# 正式评测记录 · 2026-10-10

受测版本：Git `4ae701bff993339dd7ab2c50c064d927ff17eae9`，graph v16 / prompt v16。`freeze.json` 保存受测源码、发布语料、输入、标签及评分标准的哈希；`source/` 是冻结快照。当前轮只新增评测工具和报告，生产实现没有根据本轮结果修改。

## 数据与运行

- [预注册](../../formal/protocol.md)、[80 轮输入](../../formal/data.json)、[AI 证据标签](../../formal/labels.json)、[连续会话](../../formal/continuous.json)、[评分标准](../../formal/rubric.md)。标签不进入受测模型。
- `smoke/`：4 单元，包含真实 FastAPI 会话、持久化、幂等与授权验证。
- `development/`、`test/`：各 40 个固定输入 × B/C/D/H 四策略。
- `rag/`：12 个预选输入 × memory/classic/full；单次生成不修复，独立于完整图成功率。
- `continuous/`：4 会话 × 3 轮，真实上一轮回答进入下一轮；与固定集共享组，不能增加独立样本数。
- `retrieval/`：精确定位与 R1/R2/R3 分开；相关性池独立 AI 评阅，未知不冒充负例。
- 每阶段 `inputs.json`、`manifest.json`、`rows.json`、逐例 `.jsonl` 保留输入、实际请求与可见响应、原始 usage、证据、工具事件、修复前后候选及失败。思考正文不公开，保留长度与哈希。
- `ledger.json` 是共享请求/token 预算累计，包含失败；估价见 `price_table.json`，不是账单。

B 为固定初始证据，C 为规则补检索，D 仅关闭生产精确目标快捷分支，其余固定分支仍保留；H 为当前生产混合图。D 的评测专用源文件保存在 `source/D-graph.py`。四组每题并发，启动顺序轮换；P50/P95 是这种负载下的测量，不是串行 SLA。

## 离线复算

在仓库根目录使用项目 `.venv`：

```bash
.venv/bin/python -m evaluation.formal_report
python evaluation/formal_plot.py
.venv/bin/python -m evaluation.formal_delivery
.venv/bin/python -m evaluation.formal_retrieval --help
```

`formal_report` 会加载 `review-manifest.json` 中的匿名评分与解盲键；图表由系统 Python 的 matplotlib 从同一表生成。离线复算不调用外部模型、不需要 API 密钥。`analysis/` 为派生表；不要修改原始 rows/jsonl/评分。

## 重新运行真实模型

使用新的输出目录，避免覆盖这次记录。API 别名、缓存及采样可变化，配置可重放不代表输出逐字相同。旧 frozen 内容必须保持一致；不要把新请求混进本次记录。

```bash
.venv/bin/python -m evaluation.formal_run smoke --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run smoke-http --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run freeze --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run development --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run test --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run rag --output-root evaluation/results/formal-replay
.venv/bin/python -m evaluation.formal_run continuous --output-root evaluation/results/formal-replay
```

阶段按顺序运行，共用 800 请求 / 5,000,000 token 上限；不要并行启动多个阶段进程以免共享 ledger 竞争。每题四策略由单进程统一预留预算。模拟准备仅用于构造知识消融输入，模拟文本不作真实回答或计入 API 成绩。

## 人工与 AI 阅读

`human/` 提供按场景排列的完整 Markdown/HTML、必要历史、实际证据及空白 CSV；只把该目录交给人工评审，勿同时交出 analysis 解盲键或 AI 分数。技术失败与被拒候选明确分开。

`blind/` 是独立 AI 匿名评阅批次，`review-manifest.json` 记录首评/复评身份与批次；AI 意见不是专家 Gold 或真人验收。初评范围、复评子集在正式运行前固定，未评项保持 pending。
