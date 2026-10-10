# 引文差异与修复失败复核 · 2026-10-10

本次复核已保存的合成实验记录，不调用模型 API。唯一原文定位现已接入校验器，用于可审计地恢复特定表示差异；原有错误记录、失败状态和实验分数保持不变，不回写旧实验结果。

## 结论与误归因更正

上一轮 `confirmation-correction / flash-low` 的引文错误**不是修复时新引入的错误**。首答已经把朱熹原文的“所獲”写成“所获”；首答同时有一段包含 6 条引文，超过 Schema 的 5 条上限。`Answer.model_validate` 首先失败，`validate_answer` 提前返回，没有继续检查引文。唯一一次修复只收到数量错误，删掉一条引用后，繁简字形差异才成为显式错误。

因此，“修复引入新错”的旧归因应更正为：**首答已有多个错误，Schema 短路隐藏了引文差异，反馈不完整，修复机会耗尽。** 这次复核不说明修复后的传统解释或现实分析正确。

## 扫描范围与数量

扫描 `evaluation/reports/autonomous-20261010/` 下全部非 `source/` 的原始 JSONL，按 `model_request.callId` 找到每次 generate/repair 对应的实际输入证据，再检查可解析模型正文中的每条引用。扫描 model_result，而非仅扫描已报告的 validation errors，因而能找到被 Schema 短路隐藏的错误。

| 单位 | 数量 |
|---|---:|
| 原始 Trace 文件 | 84 |
| generate/repair 模型响应 | 90 |
| 可解析、含 paragraphs 的回答 | 86 |
| 无法解析为完整回答的响应 | 4 |
| 可核对的引用记录 | 438 |
| 引用 ID 未出现在本次实际输入证据中 | 0 |
| 按原校验规则不匹配的引用记录 | 2 |
| 其中，仅繁简字符差异 | 2 |
| 其中，仅标点/空白差异 | 0 |
| 其中，新增文字、删掉实词或其他非等价改写 | 0 |

2 条不匹配记录来自**同一案例的首答与修复稿**，不是 2 个独立失败案例；它们对应同一引用和同一字形差异。纯标点/空白已经由原 `normalize` 忽略，因此不会独立触发这里统计的 QUOTE_MISMATCH。上述数量不能当作语义准确率：正确抄写并不证明引用支持解释，重复采样和修复稿也不是独立样本。

4 个无法解析的响应均保留，不纳入逐引文匹配分母：

- `confirmation/confirmation-correction-flash-high.jsonl` 第 11 行。
- `final-verification/dev-exact-gap-final-high.jsonl` 第 7 行。
- `final-verification/dev-project-correction-final-high.jsonl` 第 11 行。
- `release-verification/dev-exact-gap-released-high.jsonl` 第 10 行。

它们都报告 `MODEL_INCOMPLETE_RESPONSE / finishReason=length`，不能据此假定没有引文问题。

## 唯一差异的完整证据

原始记录：[confirmation-correction-flash-low.jsonl](../autonomous-20261010/confirmation/confirmation-correction-flash-low.jsonl)。

| 位置 | 已有内容或结果 |
|---|---|
| 第 11 行，generate 响应 | 第 3 段第 3 条引用已经写成“占者田无所获而凡事亦不得其所求也” |
| 第 12 行，首次校验 | 只报告 `paragraphs.1.citations` 超过 5 条；没有执行后续引文检查 |
| 第 13 行，repair 请求 | errors 中只有上述引文数量问题 |
| 第 14 行，repair 响应 | 同一引用仍为“占者田无所获而凡事亦不得其所求也” |
| 第 15 行，最终校验 | 报告 `QUOTE_MISMATCH:zhu-xi@737053:h32:line4:0f4238b3b2` |

证据 ID：`zhu-xi@737053:h32:line4:0f4238b3b2`。

模型 quote，首答与修复稿相同：

> 占者田无所获而凡事亦不得其所求也

本次输入的完整真实证据：

> 以陽居隂久非其位故為此象占者田无所獲而凡事亦不得其所求也

可以唯一定位的原文连续片段：

> 占者田无所獲而凡事亦不得其所求也

差异只有 `获 / 獲`，现有语料字符映射已包含 `獲 → 获`。`backend/knowledge.py` 的引用 normalize 只做 NFKC、标点/符号与空白处理，检索 normalize_search 才使用繁简映射。因此检索能处理这种差异，原引文校验仍然拒绝它。

## 严格的原文片段定位候选

新增 `backend/citations.py:locate_original_quote(source, quote)`，返回 `{"quote":真实原文片段,"start":起点,"end":终点}` 或 `None`。位置按 Python 字符串索引，end 不含末位。

规则：

1. 只使用现有语料 `CHAR_MAP` 的逐字符繁简映射，忽略 Unicode 标点和空白。
2. 保留数字、否定词、词序、实词和符号。不使用编辑距离、近义词、语义匹配或补字。
3. 归一后至少 2 个字符；必须能定位到原文中的连续区间。
4. 该区间重新归一后必须与候选 quote 完全相同。
5. 如有两个不同原文位置，包括重叠位置或繁简映射后合并的位置，拒绝定位。
6. 返回的是 `source[start:end]`；内部标点和空白来自原文，首尾无关标点不扩入。绝不返回模型改写后的字符串作为原文。

定位器比原 normalize 更保守：不增加 NFKC 兼容字符折叠，不丢弃数学等符号。例如 `第２爻 / 第2爻` 不由本函数转换，`甲+乙 / 甲乙` 也不视为相同。

函数只负责定位，**不选择证据、不生成 claim、不改变 evidenceId、不修改正文、不判断语义支持**。引用 ID、作者/作品归属和 claim 位置仍由原校验机制检查；应保留原始候选及定位事件，不能把修正后的数据冒充模型原始输出。

`validate_answer` 仅在 evidenceId 已知且原 normalize 检查不匹配时调用定位器。唯一定位后，真实片段重新通过 Citation Schema（包括 600 字上限）和原 normalize 校验，才写入返回 Answer 的 quote；传入 value 和 evidence 不变。定位失败、位置不唯一、增删实词等仍报告原 QUOTE_MISMATCH。

对外返回仍为 `(answer, errors)`，原有位置参数与 `issues` 参数不变；新增可选 `quote_resolutions` 收集器。每条事件包含 `method=unique_source_span`、`path`、`evidenceId`、`requestedQuote`、`sourceQuote`、`start`、`end`。即使某条引用成功定位，其他结构/归属/claim 错误仍会使整答失败；不能将定位事件当作回答通过标志。

这是对特定字形/标点表示差异的确定性处理，不是对语义错误的纠正，也不是容许模型编造引文。原有全局校验和风险边界继续生效。

## 验证与局限

运行：

```bash
.venv/bin/python -m pytest -q tests_python/test_citation_location.py tests_python/test_attribution.py
```

结果：45 项通过。包括 15 项定位边界测试、9 项校验集成测试，以及既有 21 项归属测试。覆盖实际获/獲差异、真实偏移与原文回填、标点/空白、重复/重叠匹配、实词增删、否定变化、爻位变化、语序变化、语义改写、符号变化、额外数字兼容折叠和过短引用；还检查输入不变、claim 不改、未知 ID 不借用别条证据、回填后的长度边界、旧调用兼容及其他校验继续拒绝错误答案。

这只是确定性定位能力的验证。438 条历史引用复核没有证明传统解释准确，也没有证明定位函数可以解决全部生成失败。全思考耗尽、资料与问题不相关、引用真实而解释牵强等问题需要分别实验；本轮不通过新风格规则掩盖它们。
