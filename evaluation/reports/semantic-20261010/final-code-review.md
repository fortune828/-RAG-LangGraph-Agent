# 最终代码审查 · 2026-10-10

本次为只读工程审查，未调用真实 API，未修改生产代码。审查对象是工作区最终 `graph v15 / prompt v16`，基于提交 `f277be7f880c368cd549e0f956555be982975a34` 的本轮差异；最终提交由主任务另行保存。已回退的 `answer_with_tools/respond` 不在当前生产图中。

**结论：在本次审查范围内，未发现新增的预算绕过、越权检索、引文归属绕过或旧会话数据破坏。** 发现一处随 v15 路由收紧而失配的测试，已告知主任务，主任务修正输入后单项复核通过。这个结论不表示模型解释的语义正确，也不表示全部持久化故障都经过形式化验证。

## 1. 截断响应与一次修复

| 检查项 | 源码依据 | 结论 |
| --- | --- | --- |
| 截断不能冒充成功 | `backend/llm.py:81–84`；`backend/graph.py:433–454` | 只有主生成的 `MODEL_INCOMPLETE_RESPONSE` 进入修复；即便 partial 恰好能解析为完整 JSON，`generation_error` 仍强制校验失败。HTTP、网络、预算异常原样传播，Router 的 length 没有被生成阶段捕获。 |
| 空正文可恢复 | `backend/graph.py:420–431`、`:462–475` | 保存可见 content（包括空串或 null）；repair 不传递空 assistant 消息，只发送原问题、材料和截断反馈。成功后清空 `generation_error`。 |
| 不会自动多次修复 | `backend/graph.py:458–475`、`:488–491` | repair 正常返回或再次 length 都把 `repairs` 加一；下一次 validate 在 `repairs >= 1` 时结束。再次截断不被接受，也不进入第二次自动修复。 |
| 失败也计费、预算不重置 | `backend/evaluate.py:34–76`；`evaluation/paired.py:72–94` | repair 包装器的 parent 指向原预算，检查及结算遍历所有祖先；length 已返回的 usage 在失败路径正常结算。预算不足在 HTTP 请求前阻止修复。记录区分请求与实际计数，callId 不重复。 |
| 未知用量不伪装成零 | `backend/graph.py:427–431`；`backend/evaluate.py:55–61` | trace 缺失用量字段为 null；评测预算保留未知计数和预留额度。已报告的失败用量仍在 trace 和评测模型结果中。 |
| 不回传内部思考 | `backend/graph.py:426`、`:465–466` | 截断候选及 repair 的历史 assistant 都只带 role/content。raw partial 在 checkpoint/评测 observer 中保留，常规 trace 中没有正文或 `reasoning_content`。 |

这里的“一次”是一次图执行的自动修复上限。`backend/app.py:168–175` 允许用户对已经结束且失败的回合显式重试，重置修复次数后重新生成；这不是新引入的后台无限重试。生产 API 默认直接使用模型适配器，`BudgetedChat` 是评测预算包装器；不能把评测总 Token 额度描述成生产端已有的跨请求账单上限。生产仍由工具三次、修复一次、超时、并发和频率限制约束。

## 2. 原文唯一定位没有承担语义判断

- `backend/citations.py:11–19` 仅采用现有 `CHAR_MAP` 和标点/空白去除，不做语义匹配、编辑距离、同义改写或补字。数字、符号、否定词和字序参与匹配。
- `backend/citations.py:30–49` 收集归一后匹配对应的原文连续位置；两个不同位置（包括重叠位置）即拒绝。回填的是 `source[start:end]`，保留原文内部标点；不会拼接不连续片段。
- `backend/validation.py:139–160` 仅在已知 evidence ID 且原校验不匹配时尝试定位，定位后仍检查 Citation Schema 和原文包含关系。不会拿另一条证据修复未知 ID；不会放大引用长度上限。
- `backend/validation.py:105` 创建返回对象，`:153–158` 只修改返回对象的 quote，原始 candidate、claim 和 evidence 不变。图的 observer 同时保留原 candidate 和 `quoteResolutions`，可以核对原模型到底写了什么。
- `backend/validation.py:123–138`、`:161–173` 的必需目标、claim 必须属于正文、作者/作品归属检查仍执行。成功恢复一个繁简引用，不会豁免同答案的作者错配或另一个伪引。

必须保留的表述边界：唯一性仅针对**新增定位分支**。旧 `normalize` 已接受的引文仍走旧路径，包括去标点/兼容字符后的匹配；不能宣称所有成功引用都经过唯一位置确认。`claim` 的古义、现实类比和自然语言中的卦爻断言仍不由机械匹配证明；这项改动没有增加这类语义认证能力。

## 3. 精确规划、授权与恢复

- 当前 `backend/retrieval.py:9–39` 已比最初 v14 实验收紧：必须全文匹配“本卦/当前卦 + 明确原文对象”的小语法；附带现实新场景、注家提问、模糊表述均交还 Router。多卦多爻不假定上游目标配对正确。不能把 v14 的实验次数直接当作 v15 的路由性能实测。
- `backend/retrieval.py:55–83` 检查授权卦号、章节、爻位、知识层、作者/作品、实际语料存在性和未知字段。它只产生请求，不执行工具、不修改证据、不更新会话卦象。
- `backend/retrieval.py:87–107` 仅合并完整章节/爻位组合，保留不同卦、作者、作品和层；跳过已执行签名，按剩余三次尝试数截断请求批次。
- 所有自动请求仍进入 `backend/graph.py:330–374` 的原工具节点。计数、参数 Schema、去重、执行权限和证据预算未绕过；`backend/knowledge.py:126–140` 在接口层继续校验或过滤 scope。程序规划没有另建不受限制的工具入口。
- `backend/graph.py:375–385` 提前进入生成只认可实际接受进 state 的目标证据；空结果、拒绝或被证据预算丢弃的目标不会冒充已补齐。未知出处的全库范围仍须由既有服务端规则决定。

上游 `requested_targets` 仍是规则解析，不是任意复杂文献问题的完整语义解析器。当前快捷规划通过保守全文语法限制适用范围，并未宣称修好了所有复杂目标配对或传统称谓问题。

## 4. 旧会话、checkpoint 与隐私

| 边界 | 核对结果 |
| --- | --- |
| 旧答案读取 | `backend/store.py:33–45` 原样载入答案；`backend/graph.py:169–203` 保留兼容 summary/paragraphs/limitations 的历史读取。没有使用新 Answer Schema 重验已经保存的旧回答。 |
| 固定卦象与发布集 | `backend/graph.py:210–214` 沿用 session cast/release；精确规划只读现有 state。`backend/store.py:40` 对缺发布字段的旧数据使用旧发布集，没有迁移或重起卦。 |
| 跨版本失败回合 | `backend/app.py:164–167` 阻止旧 checkpoint 在新 graph/prompt 上继续执行。旧已完成会话仍能读取，用户可发新回合；这不是无缝升级旧失败执行流。 |
| 同版本恢复与显式重试 | `backend/app.py:168–181` 复用 checkpoint；结束但未通过验证的重试从生成开始，已完成工具不会重新执行。成功生成清空新 `generation_error`，避免旧截断状态继续污染验证。 |
| 错误候选不交付 | `backend/app.py:160–161`、`:182–191` 移除旧答案，对错误状态保存 failed，而不是把 fallback/partial 标作已完成回答。 |
| 本地私密记录 | `backend/app.py:31–36` 的 SQLite 私有权限、`backend/store.py:60–71` 原子保存与权限未改变；`:79–87` 的生产日志不包含 prompts/答案/工具参数。截断 trace 新增的是用量和错误码，不是 partial 正文。 |

## 5. 离线验证结果

执行：

```sh
.venv/bin/python -m pytest \
  tests_python/test_incomplete_generation.py \
  tests_python/test_repair_configuration.py \
  tests_python/test_citation_location.py \
  tests_python/test_attribution.py \
  tests_python/test_exact_retrieval.py \
  tests_python/test_router_efficiency.py \
  tests_python/test_answer_recovery.py \
  tests_python/test_boundaries.py \
  tests_python/test_migration.py -q
```

首次结果：**149 passed，1 failed，16.53 秒**。失败是 `test_migration.py:154` 使用“第二爻原文是什么？”却断言程序精确规划；v15 只对包含“本卦/当前卦”的完整语法快捷规划，该输入正确地交还 Router。已立即告知主任务，主任务将测试输入改为“本卦第二爻原文是什么？”，保留工具一次、repair 一次、错误定位、来源和固定卦象断言。

随后只复核发生变化的一项：

```sh
.venv/bin/python -m pytest tests_python/test_migration.py::test_graph_exact_tool_and_one_repair -q
```

结果：**1 passed，0.57 秒**。

覆盖包含真实检索/校验实现、HTTP MockTransport、SQLite 及新 Python 进程中的 checkpoint 恢复；没有真实模型调用。完整最终测试/构建与真实回答质量验收仍以主任务的最终报告为准。

## 6. 审查后追加的定点修正

只读审查完成后，主任务在真实 stress D 中定位到“王弼等说法”的词性误报，并单独授权修改 `ATTRIBUTION_VERBS` 及归属测试。该修正保留“说”“说法是/为/即/冒号”作为正向观点断言，仅取消名词“说法”误命中“说”。相关回归 **112 passed**；上轮 438 引文所在候选全部无差异，本轮只改变同一题首答与 repair 的同一个作者误报。详见 [离线差分诊断](attribution-diagnostic/report.md)。原始 v15 失败未重标，未调用真实 API；因此本节并不将离线校验通过宣称为新生产版本真实生成成功。
