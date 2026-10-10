# 模糊出处开发压力样例 · 2026-10-10

本样例**按 C 的离线检索失败筛选**，只用于单列观察查询改写能力。它不是留出题、随机用户样本或公平总体对照；不能用它证明 Agent 普遍优于 Workflow。题目由 AI 根据已经核对的本地文献构造，参考不是专家 Gold。没有调用真实模型、没有修改生产代码，也没有读取冻结留出输入或输出。

## 输入与可核查结果

文件：`paraphrase-stress.json`，SHA-256：`4aaaefbc848366609b918352cae4c66880039482de3f3159bab41866a57b8817`。

最多允许尝试 3 个候选，实际尝试 2 个。候选和命中全部保存于 `candidates`，正式选用的一个开发压力输入位于 `cases`；文献依据与选择过程分别位于 `references`、`selection`。

选用 `paraphrase-stress-2`：

> 我记得朱熹好像讲过一种意思：身处难关时，与地位更高的人来往，表达真实的心意比排场更重要。这段解释出自哪里？能给我原文吗？

固定乾卦静卦，追问模式、空历史；核心问题是核对一段易学解释。当前图识别为出处问题并允许全库检索，初始证据仅有乾卦卦辞、彖、大象，没有目标注释。

目标是发布集中朱熹《周易本義》坎六四，ID `zhu-xi@528287:h29:line4:99879a18e8`。原记录相关部分：

> 九五尊位六四近之在險之時剛柔相際故有但用薄禮益以誠心進結自牖之象

完整记录、固定来源 URL 和定位均已保存。提问只是现代转述，不是逐字原话；“比排场更重要”概括了薄礼与诚心的关系，核对答案仍应解释原文语境和转述的限度，不能只机械声称“朱熹确实说过这句话”。目标记录是否唯一符合主题也未经专家判定。

C 使用未修改的 `evaluation/workflow.py`：`source_query` 去作者/出处问法后，按作者 `zhu-xi`、层 `commentary`、topK=4 搜索。规范化查询为：

```text
我记得好像讲过一种意思身处难关时与地位更高的人来往表达真实的心意比排场更重要这段解释能给我原文吗
```

实际返回 **1 条**：朱熹坎六三 `zhu-xi@528287:h29:line3:030c7ed64b`，score `4.085623149242305`；坎六四没有命中。规范查询与目标索引文本的二元字组交集为空，因此词面排序不能发现目标。C 的真实离线路由只执行这一次查询，随后 ready。这证明当前词面查询存在缺口，尚未证明模型可以正确改写或会使用命中结果。

## 为什么保留而排除候选 1

候选 1 的末句是“这段解释出自哪一卦哪一爻？能给我原文吗？”。直接使用相同全库词面探针时，返回坎六三、乾上九、乾初九三个朱熹注释，依然漏掉目标。

但完整 C 路径有额外混杂：旧 `backend/validation.py:33` 将“哪一爻”误当明确的“一爻”，据此锁定本卦乾初爻并预取注解；目标已满足，C 没有实际发出词面搜索。故此候选不适合单独测查询改写，未放入 `cases`。JSON 将该次全库直接探针明确标为 `counterfactualToActualScope=true`，它不是 C 真正执行的查询。这个旧解析局限另列为发现，本次不修改。

候选 2 只将末句改成“出自哪里”，排除爻位误识别后，才满足“实际 C 发出搜索但目标缺失”的选用标准。没有继续尝试第三种表述，也没有按模型结果反复挑选。

## 复现和后续比较边界

开发构造时用生产图、原 C policy 和本地 smoke 输出捕获初始状态与规则决策；没有网络模型请求。模拟正文和 Token 不作为真实生成或实验指标。真实工具参数、完整返回记录、初始证据、规则决策、相关代码哈希都保存在 JSON。

离线重算选用输入的词面结果：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python - <<'PY'
import json
from pathlib import Path
from backend.knowledge import canonical_tool_args, execute_tool
from evaluation.workflow import source_query

data = json.loads(Path('evaluation/reports/semantic-20261010/fixtures/paraphrase-stress.json').read_text())
case = data['cases'][0]
args = canonical_tool_args('search_knowledge', {
    'query': source_query(case['currentQuestion']), 'topK': 4,
    'layers': ['commentary'], 'authorIds': ['zhu-xi'],
})
result = execute_tool('search_knowledge', args, data['knowledgeRelease'], None)
print(json.dumps(result['hits'], ensure_ascii=False, indent=2))
PY
```

后续若运行 C/D，两组必须共享输入、初始证据、知识库和资源预算；只通过既有 fixture 白名单传入问题、卦象和历史，不能把 `references`、`selection`、目标 ID、候选命中或评分说明传给模型。观察应包括查询是否有意义、是否找到相关原文、是否准确解释用户转述，以及额外请求的成本。D 找到目标只说明这一特意筛选表述上的能力；D 未找到、误认其他爻或生成错误也须完整保留。结果单列，不并入先前留出或整体 C/D 成功率。
