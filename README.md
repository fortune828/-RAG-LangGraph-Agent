# 易经 RAG · LangGraph Agent

以《易经》原典和历代注解为知识基础的对话项目。Python 程序随机抽取硬币并确定性计算卦象，结合结构化检索、受控工具调用和多轮上下文生成回答。

项目用于学习与展示 LLM 应用工程：关注检索、工具决策、证据验证和可复现实验，不评测占卜灵验程度，也不将卦象作为现实预测。

**技术栈：** Python · FastAPI · LangGraph · Tool Calling · BM25 · React / TypeScript。

## 核心能力

- **确定性计算：** 给定六爻值，程序计算本卦、变卦与动爻；同会话固定卦象，新问题新建会话。
- **结构化 RAG：** 精确原文定位、中文双字切分、简繁检索归一、倒排召回、BM25 加短语奖励；按工具能力支持作者、作品、知识层、卦爻等条件。没有使用向量数据库。
- **混合执行：** 基础证据由程序准备，部分精确文献目标按规则取证；其他缺口允许模型请求 `get_passages`、`search_knowledge`。
- **受控调用：** 参数校验、范围限制、重复请求检测、每轮最多三次工具尝试和一次自动输出修复。
- **证据验证：** 检查证据 ID、引文文本、claim 位置及部分明确归属；校验通过不代表解释语义正确。
- **会话恢复：** JSON 保存业务会话，SQLite 保存 LangGraph 检查点；用户更正在助手回答失败后仍保留。

## 执行链路

```text
浏览器 → FastAPI → 新建会话、随机起卦并保存
                    ↓ 提交本轮问题
              prepare：历史、基础证据、权限
                    ↓
              直接生成 / 规则或模型路由
                              ↕
                         后端执行检索工具
                    ↓
              generate → validate → 保存并返回
                            ↓ 失败
                       repair 一次 → 再校验
```

模型提出工具请求，Python 后端负责实际执行。普通首答不一定经过模型路由；零工具调用不等于没有检索。风险与新话题另有受控分支。

## 本地启动

本地验证环境为 Python 3.10.12、Node.js 20.20.2。需要 Python虚拟环境、Node.js/npm，以及支持当前请求参数的 DeepSeek API 账户。具体依赖版本见 `requirements.txt` 和 `package-lock.json`。

```bash
git clone https://github.com/fortune828/-RAG-LangGraph-Agent.git
cd -- -RAG-LangGraph-Agent
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
cp .env.example .env.local
```

在 `.env.local` 填写 `DEEPSEEK_API_KEY`，已有配置请勿覆盖。密钥只由后端读取，不使用 `VITE_` 前缀。

```bash
npm run dev
```

访问 http://127.0.0.1:3001 。该命令构建前端并启动 Python 后端；已有构建可运行 `npm start`。默认本机监听、单进程运行；远程开发请转发3001端口。前端热更新可另用 `npm run dev:ui`。

当前记录版本为 **graph v18 / prompt v16**，知识库 `knowledge-v3-20260926-r1`。默认模型 `deepseek-flash`，Thinking low，completion 上限8192；一次修复关闭Thinking。模型别名与参数是否可用以账户实际API支持为准，配置见 `.env.example`。

## 测试与评测

```bash
.venv/bin/python -m pytest -q
npm test
npm run lint
npm run build
# 离线复算已有实验，不调用模型
.venv/bin/python -m evaluation.context_results
```

2026-10-11记录：Python 375项、TypeScript 41项通过，lint/build通过。详见[验证记录](evaluation/reports/context-20261011/verification.json)。这些是历史工程检查结果，不等于用户体验通过。

- [最新修复与随机对话实验](docs/reports/context-repair-20261011.md)：真实随机起卦、上下文对照、负面结果、费用和复现入口。
- [完整真实回答](evaluation/reports/context-20261011/human-random/materials.md)：午饭、理发连续追问，保留表现不佳的答案。
- [正式效果评测及覆盖修订](docs/reports/final-evaluation-20261010.md)：固定40+40中78题无动爻，不能代表完整随机起卦产品效果。
- [RAG / Agent / 模型配置实验](docs/archive/experiments/autonomous-optimization-20261010.md)：比较规则与自主检索，不预设Agent更好。

当前仍存在重复解卦、牵强类比和助手推测进入后续回答的问题。真实引用不能保证正确解释；AI评分、人工评分、机械校验分别报告。真实API复跑会产生费用，使用各实验文档中有预算限制的入口。

## 目录与文档

| 路径 | 内容 |
|---|---|
| `backend/` | 当前Python后端、图、检索、模型适配、校验与存储 |
| `src/`、`shared/` | 前端与共享数据契约 |
| `data/` | 固定知识发布、来源、原始快照和审查记录 |
| `tests_python/`、`tests/` | Python与TypeScript回归测试 |
| `evaluation/` | [评测代码与数据索引](evaluation/README.md)，保留合成输入与可复查原始结果 |
| `scripts/` | 语料构建、知识审查及历史评测工具 |
| `server/` | 历史TypeScript后端，当前启动不使用；保留用于迁移记录与相关测试 |
| `docs/` | [文档导航](docs/README.md)：当前设计、主要报告和历史归档 |

项目定义：[SPEC](docs/SPEC.md) · [架构](docs/architecture.md) · [任务清单](docs/tasks.md) · [验收规则](docs/acceptance.md) · [评测计划](docs/evaluation-plan.md)。此前README开发说明归档至[历史记录](docs/archive/README_HISTORY.md)。

## 数据、隐私与使用说明

`.env.local`、`runtime/`、`evaluation/results/`、依赖目录和构建产物不提交。`runtime/`包含真实会话和检查点；公开实验记录使用合成输入，不能据此默认公开真实用户聊天。

知识库当前包含964条原典、112条试点注解，尚未完成专家逐条审定。来源与条款见[data/SOURCES.md](data/SOURCES.md)、[知识库说明](data/knowledge/README.md)及各来源记录。部分上游数据注明学习交流/非商业用途，其他来源和工具数据另有条款。**本仓库未为全部代码与数据授予统一开源许可证，也不声明整套语料可商用。**
