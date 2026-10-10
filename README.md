# 见素问微 · Python / LangGraph 周易情境解读 Agent

回答目标修订（2026-10-10）：所有普通解卦都从本次卦象出发，现实条件决定建议的分寸；小事轻解、大事深谈。首次解读包含简短有据的卦意，追问可自然承接，不堆原文、不以吉凶保证或否定日常行动。当前 graph v9 / prompt v14，保留单核心问题、真实引用与风险支持边界。见 [本轮验收](evaluation/cast-grounded-20261010.md)。

面向现实困惑的占卜对话应用：程序起卦，以真实材料理解卦象，用隐喻帮助看清处境、取舍与下一步。回答追求沉稳、通透、具体，不以资料汇报代替解卦，也不宣称掌握确定的未来。

**当前状态（2026-10-08）**：核心运行后端已迁移为 Python + FastAPI + LangGraph，现有 React 前端保留。迁移目标是让只掌握 Python 的开发者学习、调试并解释 Agent 核心；前端开发不作为学习前置条件。产品质量完善与冻结留出评测仍在进行，历史 TypeScript 后端保留作对照和回滚。

当前为 graph v9 / prompt v14、Flash 思考模式。单核心问题会话、角色明确的近期对话、引用校验和同轮恢复已实现；101 项 Python 回归通过，真实回答仍有语义验收缺口。最新结果及版本差异见 [本轮回答体验验收](evaluation/cast-grounded-20261010.md) 和 [任务状态](tasklist.md)。

## 启动

需要 Python 3.10+、Node.js 20.20+（Node 只用于前端构建和历史工具）。Python 依赖固定在 requirements.txt。以下命令在项目根目录执行：

```bash
python3.10 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci
# 首次配置时复制 .env.example 为 .env.local，填入 DEEPSEEK_API_KEY。
# 已有 .env.local 不要覆盖。
npm run dev
```

打开 http://127.0.0.1:3001 。`npm run dev` 构建前端并启动 Python 后端。已有构建时使用 `npm start` 或 `.venv/bin/python -m backend`。服务只监听本机，单进程运行。远程开发时转发 3001 端口，保持服务终端运行。

如果系统缺少 venv 模块，先安装对应 Python 的 venv 支持；当前工作区已准备好 `.venv`。不需要改变系统默认 Python。前端热更新可另运行 `npm run dev:ui`，通过 3000 端口代理 Python API。

每个聊天只服务一个核心问题。同题的补充、纠正、选项比较及出处追问继续使用原卦；新的独立问题必须新建聊天、重新起卦。新建操作不继承旧对话，也不自动删除旧会话；删除仍使用单独入口。危急风险先提供必要安全支持。

默认模型为 `deepseek-flash`，可通过 `.env.local` 的 `DEEPSEEK_MODEL` 覆盖；修改后重启后端生效。当前使用思考模式（`thinking.type=enabled`）、reasoning_effort high、max_tokens 8192；不再发送 temperature。

密钥只在后端读取；不要使用 VITE_ 前缀。会话和检查点保存在 runtime，不能公开提交。浏览器沿用既有访问令牌。

## 核心执行路径

```text
React → FastAPI → 服务端起卦并保存会话
                  ↓
           LangGraph prepare（固定取证、上下文、意图与范围）
                  ├─ 明确换题／重起卦 → finish（固定操作提示，零模型调用）
                  ├─ 普通首次解读／风险支持／已确认资料缺失 → generate
                  └─ 追问或首次明确缺失出处 → route → tools → route（有界）
                                      ├─ 识别独立新问题 → finish（新聊天提示）
                                      ↓
                                  generate
                                      ↓
                                  validate
                                  ├─ 合格 → finish
                                  └─ 一次 repair → validate → finish/技术失败
```

- 起卦采用三枚硬币六次投掷；Python `secrets.randbelow` 抽样，纯函数计算卦象。新会话额外保存每枚硬币结果与规则版本。
- 精确检索提供本卦、动爻和必要变卦材料；BM25 补充词面检索，搜索时简繁归一，引文校验不替换简繁。
- 工具只有 `get_passages`、`search_knowledge`。参数由 Pydantic 校验，服务端约束卦象范围；明确未知出处查询可按受控策略搜索全库。
- 每轮最多三次工具尝试、一次缺口反馈、一次输出修复。新生成段落必须声明 kind：interpretation 必须引用，reflection 禁止引用；传统解释和现实建议分段。未实现完整语义蕴涵验证。
- 最终生成接收核心问题、当前问题、有限近期用户／助手对话、相关原文和自然语言限制，不接收原始工具日志。对话至多六轮、6000 字符，单轮过长回答保留标记节选，预算按实际传入内容计算；旧助手意见不是用户事实或可直接引用的证据。按元数据区分资料未收录与尚未取得；未知书名和搜索零结果不直接证明全库缺失。
- 风险支持及要求重起卦时不提供古文证据，不进入工具循环；重起卦必须由用户显式操作。规则识别仍有限，不是完整风险分类器。
- StateGraph 使用 SQLite 持久 checkpoint，**每个 turn 一个 thread**。最近至多六轮业务历史作为新轮上下文；失败轮按原 thread 恢复。已完成节点不必重跑，正在进行且尚未保存结果的模型请求仍可能重放，不宣称外部 API 恰好调用一次。
- 结构／引用修复耗尽属于技术失败，页面不展示无效正文；即使图已结束，重试也会在原 thread 从生成节点重新执行，保留证据而不重跑工具。真正的资料不足仍是正常业务回答。旧 checkpoint 版本不兼容时保留历史，改用同卦新追问，不强行迁移状态。
- 业务幂等由 session/turn requestId 和本地存储保证；checkpoint 不替代业务存储。删除会话同时清除其 Python 图线程。

## 你需要学习的代码

| 顺序 | 文件 | 要能解释的问题 |
|---|---|---|
| 1 | backend/graph.py 的 build_graph | 状态、节点、条件边如何组成 Agent？ |
| 2 | backend/llm.py、prompts.py、models.py | 模型如何请求工具？结构化输出怎样约束？ |
| 3 | backend/knowledge.py | 固定取证和按需检索有何区别？为什么限制范围？ |
| 4 | backend/validation.py | 引文真实为什么不等于解释正确？如何修复或降级？ |
| 5 | backend/app.py、store.py | 业务幂等和 checkpoint 各负责什么？重启如何续跑？ |
| 6 | tests_python/、backend/evaluate.py | 如何复现失败、验证恢复、控制费用并诚实评测？ |

先让助手实现并验证，再按上述路径结合真实 Trace 学习。建议亲自做三个小练习：增加一条路由判断、调整一种检索策略、注入一次生成故障并观察恢复。无需从 React 或 TypeScript 开始。

## 验证与评测

[评测系统审计与最小实施提案（2026-10-10）](evaluation/evaluation-audit-20261010.md)已完成；正式对照、数据标注和采集扩展等待确认。以下现有命令仍属于工程契约或开发验证，不代表冻结留出评测。

```bash
npm run test:python                   # Python、API、真实 StateGraph 与跨进程恢复；mock 模型
.venv/bin/python -m backend.evaluate  # 64 项精确检索契约，不请求模型
npm run lint                         # 保留前端/历史 TS 类型检查
npm test                             # 历史 TS 37 项基线
npm run build
npm run knowledge:audit              # 保留的数据来源/修订审计，不请求模型

# 显式执行才消耗模型额度，固定合成开发样例；不是生产随机起卦或留出评测
.venv/bin/python -m backend.evaluate --live --max-calls 8 --max-tokens 100000

# 25 个合成开发场景：先离线；--live 才请求真实模型，仍不是正式留出评测
.venv/bin/python -m evaluation.development_checks
.venv/bin/python -m evaluation.development_checks --live --max-calls 40 --max-tokens 160000
```

本次迁移验证：

- TypeScript 类型检查、原 37 项基线、前端生产构建与知识来源审计通过。
- Python 14 项测试通过，包含 4,096 种六爻组合和必要证据预算、API 幂等/并发/访问隔离、工具范围、修复反馈与删除检查点。
- 分别验证模型异常和进程突然退出后的新 Python 进程恢复：保留同卦、同轮次，已完成工具节点不重跑。
- 真实浏览器 + Python API + mock 模型通过：首次解读、第二爻工具查询、引用展开、刷新、390px 手机视口、删除；这不是模型质量评分。
- Python 真实模型三轮开发冒烟完成：6 次请求、28,901 reported tokens；首次解读、零工具的生活追问、一次真实 get_passages 补检索均返回 answered、机械校验通过、卦象保持。不代表正式人工语义评分或泛化成功率。

迁移报告：[真实模型原始记录](evaluation/results/python-live-20261008-070030-561f7e/report.json)、[浏览器记录](evaluation/results/python-browser-1791442819647485130/report.json)。results 是本地忽略目录，公开展示时须另整理可分发证据。

当前 Python evaluator 是有费用预算的开发冒烟；旧 `npm run eval:live` 仍运行 TypeScript 基线。两者不得混算。新版 40+40 生活化留出集、B/C/D 对照、人工评分尚未完成，不填未经测量的质量提升百分比。

## 数据、兼容与回滚

默认知识版本仍为 `knowledge-v3-20260926-r1`，1,076 条证据：964 条原典、112 条王弼/程颐/朱熹试点爻注，包含 12 条可追溯修订。没有改动发布包。旧会话继续绑定旧 release；缺 release 的历史会话绑定原 V2。注解覆盖有限，人工专家审核数为 0。

原始快照、解析、构建与校勘脚本暂保留在 scripts/knowledge（TS/Python），不要求迁移这些离线工具才能使用 Python 后端。来源与用途限制见 [data/SOURCES.md](data/SOURCES.md) 和 [知识修订交付](docs/KNOWLEDGE_CORRECTIONS_20260926.md)。数据包含学习/非商业限制，不宣称拥有完整商业发布授权。

```bash
# 先停止 Python 服务，避免两个后端同时写同一 runtime
npm run dev:legacy
# 或已有前端构建时
npm run start:legacy
```

回滚使用旧 TypeScript 服务；旧服务能读兼容业务 JSON，但不会执行 Python 图的 checkpoint 续跑。若必须回滚一个中断的 Python 轮次，旧服务会从整轮重新生成；原卦仍保留。旧服务删除会话不会清除 Python checkpoint，因此隐私删除应在 Python 服务完成。不要同时运行两个后端指向同一个数据目录。

## 当前边界

这是单实例本地演示系统，未引入多用户账户、分布式锁、完整长期记忆、向量检索或八字。意图/风险识别当前为规则初筛与生成约束，不是完整安全分类器。无动爻只显示本卦；新记录可展开实际单币结果，旧记录不会补造投币过程。

引用校验仍是有限机械规则；作者覆盖说明已与正向观点断言作初步区分，但复杂转引、委婉归属与语义支持仍需人工评测。近期版本重点是能演示、能解释、能亲自修改的 Agent 工程闭环，持续投递不必等所有扩展完成。

规划以 [SPEC.md](SPEC.md) 和 [architect.md](architect.md) 为准，docs 中 9 月文档保留历史事实。LangGraph checkpoint 设计参考[官方 Python 持久化文档](https://docs.langchain.com/oss/python/langgraph/persistence)。
