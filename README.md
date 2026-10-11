# 见素问微 · Python / LangGraph 周易情境解读 Agent

当前修订（2026-10-11）：**graph v18 / prompt v16**。修复混合原典/注解目标在生成前丢失、全库查出处把无关初始材料当成已取得的问题；增加有限的显式卦爻事实核验，并修复回答失败后丢弃该轮用户更正的问题。历史仍保留最近六轮，失败轮只保留用户输入，不纳入失败助手候选。消息重排、证据缩减等候选未证明稳定改善，未上线；保持 Flash low 配置与 prompt v16。新体验评测每个会话经真实 API 随机起卦，保留硬币结果、不改爻、不挑卦，后续沿用同一会话。机器检查、知识语义审查、四维人工体验分开，新增三个 CTX 标签。见[本轮修复与实验](evaluation/context-repair-20261011.md)。下文带旧日期的记录保留为历史，不代表当前验收状态。


正式效果评测（2026-10-10）：生产冻结在 Git `4ae701b`、graph v16 / prompt v16。本轮完成 40 开发 + 40 独立测试的 B/C/D/H 对照，知识消融、检索与连续对话分表报告；生产行为未按结果修改。独立 AI 评阅不等于专家 Gold 或真人验收，该报告当时人工评分留空；2026-10-11首批14份已收集并独立整理。见[最终效果报告](evaluation/final-evaluation-20261010.md)、[匿名人工材料](evaluation/reports/formal-20261010/human/README.md)和[项目展示总结](evaluation/project-showcase-20261010.md)。下文旧轮次的“待正式评测”描述为历史状态，以本条与最终报告为准。


语义与可靠性修订（2026-10-10）：当时为 **graph v16 / prompt v16**，仍使用 Flash low / 8192 主调用与同模型非思考一次修复。默认爻注限实际动爻或明确所问爻位；纯文献精确请求由程序取证，其他问题保留模型路由；提供真实爻名，唯一原文定位处理繁简差异，主生成截断可进入原有一次修复。四题隔离确认冻结在 v14，v15 另收紧自动取证以保留换题识别，版本不混算；v16 另修正“说法”名词被当作观点动词的误拒，只离线重校验旧候选。结果、负面实验及完整回答见[本轮报告](evaluation/semantic-optimization-20261010.md)。语义质量仍待用户验收。

上一轮提示词减法修订（2026-10-10）：当时为 graph v10 / prompt v16，保留普通段落与 claim 引用契约。清理 SYSTEM、ROUTER、RISK_SYSTEM、动态指令、repair 和工具描述；技术约束继续由程序保障。生成机制、Schema、校验器和前端保持，表达质量以真实回答复核，见[本轮完整提示词、删减与对照](evaluation/prompt-subtraction-20261010.md)。

面向现实困惑的占卜对话应用：程序起卦，以真实材料理解卦象，用隐喻帮助看清处境、取舍与下一步。回答追求沉稳、通透、具体，不以资料汇报代替解卦，也不宣称掌握确定的未来。

**当前状态（2026-10-08）**：核心运行后端已迁移为 Python + FastAPI + LangGraph，现有 React 前端保留。迁移目标是让只掌握 Python 的开发者学习、调试并解释 Agent 核心；前端开发不作为学习前置条件。产品质量完善与冻结留出评测仍在进行，历史 TypeScript 后端保留作对照和回滚。

当前为 graph v18 / prompt v16、Flash low 思考模式。新回答仅包含 status、普通 paragraphs（text 与 citations）及可选内部 limitations；不再生成 summary 或段落 kind。引用用 claim 定位本段受支持的文字；前端在该处显示引用标记。旧会话的摘要、正文和限制完整展示为普通正文。质量与验证记录见 [本轮回答体验验证](evaluation/natural-dialogue-20261010.md) 和 [任务状态](tasklist.md)。

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

默认模型为 `deepseek-flash`，可通过 `.env.local` 的 `DEEPSEEK_MODEL` 覆盖；修改后重启后端生效。默认 `DEEPSEEK_THINKING=enabled`、`DEEPSEEK_REASONING_EFFORT=low`、`DEEPSEEK_MAX_TOKENS=8192`；不发送 temperature。可选 `disabled / 4096` 降低开发调用成本，此时不发送 reasoning_effort。本轮未证明该可选配置保持对话质量，未将其设为生产默认。模型响应中的 completion 含思考与正文，总上限须一起考虑。一次校验修复使用同一模型、disabled / 8192；没有增加额外修复轮次。显式实验配置默认全阶段一致，只有 final profile 单独声明 repair 配置。

密钥只在后端读取；不要使用 VITE_ 前缀。会话和检查点保存在 runtime，不能公开提交。浏览器沿用既有访问令牌。

## 核心执行路径

```text
React → FastAPI → 服务端起卦并保存会话
                  ↓
           LangGraph prepare（固定取证、上下文、意图与范围）
                  ├─ 明确换题／重起卦 → finish（固定操作提示，零模型调用）
                  ├─ 普通首次解读／风险支持／已确认资料缺失 → generate
                  └─ 追问或首次明确缺失出处 → route → tools → route（有界；明确目标齐备则直接 generate）
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
- 每轮最多三次工具尝试、一次缺口反馈、一次输出修复。普通段落允许混合卦义与现实分析，引用 claim 必须逐字出现在正文中，quote 必须匹配本轮证据；明确作者、作品及卦爻目标仍校验。位置与原文匹配不能证明解释语义或现实建议正确。
- 最终生成接收核心问题、当前问题、有限近期用户／助手对话、相关原文和自然语言限制，不接收原始工具日志。对话至多六轮；以序列化后 16000 字符为助手摘录压缩目标，优先保留这六轮的用户原话（大量转义字符可使用户文本自身超出此目标）；单轮助手正文连同旧摘要与内部限制超过 4000 字符时保留首尾并标记中间省略，预算按实际传入内容计算；旧助手意见不是用户事实或可直接引用的证据。按元数据区分资料未收录与尚未取得；未知书名和搜索零结果不直接证明全库缺失。
- 风险支持及要求重起卦时不提供古文证据，不进入工具循环；重起卦必须由用户显式操作。规则识别仍有限，不是完整风险分类器。
- StateGraph 使用 SQLite 持久 checkpoint，**每个 turn 一个 thread**。最近至多六轮已完成或失败轮的用户输入作为新轮上下文；助手正文只取成功完成轮。失败轮按原 thread 恢复。已完成节点不必重跑，正在进行且尚未保存结果的模型请求仍可能重放，不宣称外部 API 恰好调用一次。
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

已按批准的[审计提案](evaluation/evaluation-audit-20261010.md)建设第一阶段 C/W1 与 D/W2 评测：14 条固定开发输入、完整可见调用记录、可重算统计和 14 份匿名人工试评材料。见[第一阶段验收](evaluation/phase1-acceptance-20261010.md)与[运行说明](evaluation/phase1/README.md)。这是历史 graph v9 / prompt v14 开发对照。旧 14 份匿名评分已暂停，不再填写原 scores.csv，也不作为正式评分；原回答、Trace 和统计保留。当前重构独立记录生成对照、连续追问与独立 AI 体验，不冒称真人评分。B、检索层对照及 40+40 冻结集尚未实施。

```bash
npm run test:python                   # Python、API、真实 StateGraph 与跨进程恢复；mock 模型
.venv/bin/python -m backend.evaluate  # 64 项精确检索契约，不请求模型
npm run lint                         # 保留前端/历史 TS 类型检查
npm test                             # TS 基线与新旧回答渲染回归
npm run build
npm run knowledge:audit              # 保留的数据来源/修订审计，不请求模型

# 显式执行才消耗模型额度，固定合成开发样例；不是生产随机起卦或留出评测
.venv/bin/python -m backend.evaluate --live --max-calls 8 --max-tokens 100000

# 旧 C/D 的 --paired 冻结 v9/v14；在当前新版代码上会拒绝执行，不得更新旧哈希冒充同一基线
# 历史 v14/v15 对照须使用 2456f97 版本；当前源码会拒绝，防止结果错标
# --live 消耗模型额度；output 必须为尚不存在的全新目录
.venv/bin/python -m evaluation.natural_dialogue --live --output evaluation/results/natural-dialogue-new-run
# 已保存的完整真实结果可离线重算
.venv/bin/python -m backend.evaluate --rescore evaluation/reports/phase1-20261010/complete

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

Python evaluator 保留旧冻结 C/D 的离线重算，历史 Schema 由 evaluation/legacy_schema.py 独立校验，不套用 v15 起的普通段落契约；当前源码不能直接重跑旧冻结 --paired。旧 `npm run eval:live` 仍运行 TypeScript 基线，各版本不得混算。新版 40+40、完整 B/C/D 与真人评分仍待后续验收，不填未经测量的质量提升百分比。

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

当前 v15/v16 提示词对照（真实 API，使用全新输出目录）：

```bash
.venv/bin/python evaluation/reports/prompt-subtraction-20261010/run.py --live --output evaluation/results/new-prompt-comparison
```

四组对话与两组边界输入固定卦象、证据和历史；另比较一次检索路由决策。无改写 Schema、无生成后润色。历史结果保留，新旧版本不混算。

本轮实验复算：`.venv/bin/python -m evaluation.autonomous_review --usage`；付费复跑及完整输入见[实验索引](evaluation/reports/autonomous-20261010/README.md)。旧阶段基线不可与当前结果混算。
