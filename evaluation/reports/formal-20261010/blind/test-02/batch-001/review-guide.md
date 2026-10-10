# 匿名独立评阅要求

使用本包 materials.json 或 materials.md。只评所给匿名输出，不查策略或生产代码，不调用被测 API，不读其他评阅分数。
本说明缩减输出冗余，不改变随包 rubric.json 的冻结锚点、N/A、taskSuccess 和引用支持口径；完整口径优先。

输出一个 JSON 对象：reviewer 为身份对象（reviewerId、kind=AI、expertStatus=non_expert、blind=true、independent=true），results 为逐 answerId 记录数组。
每条保留 answerId、rubricVersion、taskSuccess、taskSuccessReason、evaluations；存在未交付候选时另写 candidateTaskSuccess/Reason。未交付的实际 taskSuccess=false，不以候选成功替代。
evaluations 按 target=delivered/candidate 分开。每维 dimensions.<name> 写 {value:0/1/2/null,rationale:具体理由}，null 另写 naReason；理由引用原句或位置。五维不求总分。

## 五维

- understanding：是否准确理解本轮目标、已知条件、真实历史和最新纠正，并保留未知事项的未知性质？
  0：主要目标、责任、角色或最新纠正被忽略、颠倒，回答实质建立在错误前提上。 1：大体理解，但遗漏一项有影响的条件、更正不充分，或加入局部未给定事实；仍能辨认用户真正所问。 2：目标和关键事实准确；最新纠正被实际采用；没有把假设、担忧、建议或旧助手推断当成既定事实。
- knowledge：原典词义、爻名、作者归属、注家观点及适用条件是否忠实，而不只是引文字面相同？
  0：核心文义、所问对象或作者归属错误；主要解释与可核对材料冲突，或将无依据解释冒作核心传统结论。 1：正确与明显不足并存，例如局部误释、条注范围扩大、漏掉影响解释的条件或归属含混。 2：在可核对范围内，主要传统断言、解释和条件准确，作者及对象不混淆。
- integration：现代联系是否有相关结构、回应实际条件，并清楚区分文化启发与事实、心理、因果或结果判断？
  0：核心应用主要依赖强行对应、占象确认事实或心理、无依据预测与保证，导致明显失当的判断；或明确要求应用而实质完全未回应。 1：有合理联系，但部分装饰性、牵强、等同过强，或局部用象义替现实事实与条件背书。 2：类比结构贴切、能帮助理解取舍，适用边界清楚，现实判断由现实事实支撑。
- conversation：回答是否自然承接当前对话，让用户容易理解；展开程度、语气和后续方向是否符合当下需要？
  0：明显不连贯、难以理解，持续无视当前对话方向，或以指责、强行心理归因等方式严重阻碍交流。 1：基本可交流，但有明显跳接、过度纠正、机械铺陈、无益重开已解决事项或语气与情境不匹配。 2：连贯、明确、尊重，复杂问题有适当展开，简单或已收尾问题也能清楚推进或结束。
- helpfulness：是否完成用户实际需要的查证、判断或下一步，并提供所需的依据、取舍或可执行支持？
  0：没有实质帮助，方向主要错误，或只有泛化核验失败/无关内容；诚实承认不足本身不足以获得高分。 1：有实用部分或可信线索，但仍缺关键步骤、条件、依据或收尾判断；需要明显补充才能完成所问。 2：核心需求得到充分帮助；查证给出所需对象与证据，建议基于实际条件，决策有清楚的理由、取舍或有效下一步。

## 错误和证据支持

每个 evaluation 的 errors 保留实际错误的 type、quote（答复原句）、location、reason、certainty；有条件时注明 severity 与 dimensions。只标实际问题，不为填表造错；未知标签可保留。
faithfulness.claims 覆盖材料 citationClaims 中每个 occurrence，并补明显未引用的传统断言。混合 claim 拆成传统/现代命题；同义重复可合并但列出全部 occurrenceIds。
每条写 claimId、occurrenceIds（未引为空）、assertion、quote（答复原句）、location、layer、origin、sourceIds、supportLabel、rationale、contradiction、quotationMatch；有原文支持时附 evidenceQuote。
supportLabel 使用 supported/partial/unsupported/unknown。纯现代类比等不进入传统分母时才用 null，并写 excludedFromTraditionalDenominator=true 及 exclusionReason。真实引文不自动证明 claim 含义。
faithfulness.coverage 保留 citationClaimOccurrencesTotal/Reviewed、traditionalAssertionsIdentified/Reviewed、uncitedTraditionalAssertionsIdentified/Reviewed、uncitedDiscoveryExhaustive=false 和 notes。不能把未检查的 claim 算已核对。
evidence 是生成实际看见的资料。末尾 referenceOnlyEvidence 可辅助独立查证，但生成未见：不能把它算作已检索、已引用，不能据此把诚实未取得材料说成虚构缺失。引用支持若只来自补充参考，注明 supportEvidenceScope=reference_only。
无实质交付时对应五维 N/A 有原因；候选可另外评分。内部 fallback 仅是引擎诊断，不证明用户看到了它。

不使用字数、段落数、引用数或固定句式作为自然度/帮助评分指标。AI 评阅不是人工或专家 Gold。

错误类型：fabricated_fact, unsupported_psychology, missed_correction, history_contradiction, wrong_attribution, wrong_classical_meaning, scope_overgeneralization, citation_mismatch, strained_analogy, unsupported_prediction, unsupported_requirement, unhelpful_expansion, task_not_answered, misleading_missingness
