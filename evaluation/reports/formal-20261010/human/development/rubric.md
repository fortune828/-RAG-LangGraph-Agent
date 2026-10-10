# 人工试评说明

先读 materials.md 或 materials.html；以当前问题、完整历史、卦象和提供原文为依据，填写 scores.csv。不要读取分析目录中的解盲键或既有 AI 分数。

五维均为 0（明显不满足）、1（部分满足）、2（明确满足）：

- understanding：理解本轮问题与用户更正，区分已知事实与推测。
- knowledge：传统解释忠于提供原文及作者归属；真实引用不自动证明解释正确。
- integration：卦意参与分析且联系自然，不牵强映射或只作装饰。
- conversation：自然交流，篇幅和深度适合实际问题，追问承接上文。
- helpfulness：回应真实矛盾，分析条件、取舍与行动的实际帮助。

表中只有 answerId 为匿名占位，未代表已有人工评分。每次实际评阅填写 reviewerId、target（delivered 或 candidate）和 rubricVersion，五维分别填 0/1/2；不适用填 N/A，并在该维 NAReason/Reason 说明。候选可单独增一行评分，不能混作已交付体验。段数、字数、引用条数不作为评分标准。无可见回答时分数不伪填 0，taskSuccess 为 false；未知与未提交保持区别。

完整冻结口径见同目录 rubric.json。errorsJson 使用其 errors.types 中的标签，附原句、位置与原因；保留有证据的新标签，不因不在目录就丢弃。当前标签：

fabricated_fact, unsupported_psychology, missed_correction, history_contradiction, wrong_attribution, wrong_classical_meaning, scope_overgeneralization, citation_mismatch, strained_analogy, unsupported_prediction, unsupported_requirement, unhelpful_expansion, task_not_answered, misleading_missingness

这是人工表，不会自动填入 AI 评分。AI、人工和机械指标分别汇总。
