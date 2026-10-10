# 历史第一阶段：固定开发输入 C/D 配对（v9/v14）

本目录保留旧 v9/v14 协议和案例；原 14 份匿名评分已暂停，不再填写原 scores.csv，也不作为正式评分。当前产品为 v10/v16，不能在新源码上重跑旧 --paired；行为哈希不一致应拒绝，不能覆盖旧基线。新自然对话记录见[本轮验证](../natural-dialogue-20261010.md)。

旧阶段只比较 14 个固定开发轮次。B（仅初始证据）保留后续正式范围；C/W1 是规则补检索，D/W2 是生产 Agent；W3 仅为以后按需诊断。不加载 gold，不建设 R1/R2/R3 或留出集。

## 执行

以下 --paired 命令仅适用于还原冻结 v9/v14 的独立 checkout；当前源码应拒绝。--rescore 可在当前源码使用冻结历史 Schema 重算旧统计。不要覆盖已经保存的原始回答或分数：

```bash
# 无 API 消费：检查真实图、规则工具及完整采集
.venv/bin/python -m backend.evaluate --paired --max-calls 100 --max-tokens 600000

# 明确调用真实模型：最多全局 100 次、保守计账 600000 tokens
.venv/bin/python -m backend.evaluate --paired --live --max-calls 100 --max-tokens 600000

# 替换为运行命令输出的实际目录；不请求模型
.venv/bin/python -m backend.evaluate --rescore evaluation/results/<run-id>
```

两组每题相同上限：6 次模型请求、100000 tokens、3 次工具尝试、1 次修复、150 秒。案例顺序固定，逐题交错 C/D 顺序；语料及本地索引预热，不控制供应商缓存，但保存原始 usage。全局预算是附加停止线，耗尽会保留 budget_stopped/not_run，不能称全部完成。预算按序列化字节加输出上限保守预留，不是价格或账单。

`cases.json` 只含运行输入，没有目标答案或检索必要性标签。两组每题使用完全一样的固定 session/history，后一个案例不继承前一个变体刚生成的答案；因此测的是固定输入决策和生成，不是自由演化的端到端多轮体验。groupId 保留衍生关系。

## 基线和生产边界

`../baselines/phase1-v1.json` 冻结 graph v9 / prompt v14、模型、知识库、Git 及行为文件哈希。执行器拒绝提示词、知识库、领域规则或校验文件与冻结值不一致；新行为需要另建版本和报告，不能改过代码后冒充同一基线。

评测增量只有图的可选 route_policy / observe 参数、模型响应的非敏感元数据，以及预算/记录器。生产 API 不传评测参数、不读取实验输入、不产生完整对话采集。当时图版本为行为基线 v9；代码修订和实验协议 paired-phase1-v1 另记。当时默认 D 与冻结源码的请求及结果对照已归档；当前测试检查冻结入口拒绝新行为，不能要求 v15 输出与 v14 一致。unknown 用量在 trace 中为 null。

## 产物和口径

- manifest.json / baseline.json / source/：配置、Git/dirty、代码、数据和依赖哈希及源码快照。
- inputs.json：完整固定输入；rows.json：最终状态、初始证据、路由最终证据及生成/校验实际可见证据。
- traces/*.jsonl：模型请求/响应、规则决策、工具请求/结果、预算是否计数、修复前后候选、节点更新、错误与时间。真实密钥/HTTP 认证头不采集；内部 reasoning_content 不导出，仅存存在标记、长度与哈希，运行时协议仍原样传递。这里的可复现指配置、输入、输出和统计可追溯，不是 API 输出字节级确定。
- metrics.json / comparison.csv / report.md / scoring-source.py：逐题、总体和分场景的机械与用量数据，可用 --rescore 重算。Schema 首次、最终机械校验与逐引用匹配分开；reportedSum 是已知部分，unknownCalls > 0 时完整总量为 null。离线统计版本 paired-metrics-v2 还识别请求无响应的硬中断：unresolvedModelRequests 单列，执行/计费未知，modelCalls 只计已确认，modelCallsTotal 为 null；不猜测请求是否付费。评分器源码哈希与快照独立于运行 manifest 保存。
- review/：14 份旧预选匿名输出、原 scores.csv、rubric 均保留；评分已暂停，见 PAUSED.md。不会自动填真人评分。新核心对话体验移出 fuzzy-author/source-two-lines；相关独立检索与引用回归继续保留。

工具请求和预算尝试分开：非法/重复/越界尝试可能消耗一次预算，但没有成功执行；超预算请求不再增加尝试。ok/empty 为实际检索结果，rejected 为程序拒绝；prepare 固定读取另记，不能把工具 0 次说成没有检索。

本阶段没有 gold，所以不计算 Recall、工具必要性召回、语义成功率或过度拒答率。没有完整价目表，不估算金额。mock 的 D 故意只回答 ready，用于观察漏检索/修复记录，不能拿其机械失败率评价真实 Agent。

完整运行先留在被忽略的 results/；公开前只导出已核查的合成记录到 reports/。没有真实用户 runtime，也没有推送到外部观测服务。第一阶段验收后再由用户决定是否进入检索标注和 40+40。

首轮交付见[验收](../phase1-acceptance-20261010.md)。完整合成记录在 reports/phase1-20261010/complete，外部中断轮次单列为 interrupted；两者不拼接。完整轮次另附匿名材料的 materials.md 阅读版。
