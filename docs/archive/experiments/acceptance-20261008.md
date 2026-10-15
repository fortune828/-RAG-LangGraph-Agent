# 首批实现与验收记录 · 2026-10-08

> 本文保留 graph v3 / prompt v4 的历史结论；后续修改和当前结果见 [graph v4 / prompt v5 复测](acceptance-20261008-followup.md)，不覆盖旧报告或将旧测试算作新版本验收。

本批直接修改既有 Python 图、API、校验与 React 页面，没有新增生产服务、兼容代理或通用策略框架。默认仍为 Python + LangGraph；开发验证脚本仅供合成案例运行，不参与生产请求。

验收执行者：Codex 主 agent；子 agent 分别实现前端、作者归属校验和开发验证，主 agent 阅读变更及真实回答并核对结果。**语义复核属于 AI 辅助复核，没有真人评分或专家审核，不冒充人工盲评。** 原始报告保留 `humanReviewed=false`。

## 已实现与验证

| 任务 | 结果 | 依据 |
|---|---|---|
| T01 | 已取消逐段强制引用；现金流建议可无引用，伪造引文仍拦截；语义分段仍有残留问题，未关闭 | `tests_python/test_answer_recovery.py`；最新开发报告 `life-initial` / `life-correction` |
| T02 | 通过：已结束的失败 checkpoint 也能重新生成，同卦、同轮、工具不重跑、并发重试不重复 | `test_answer_recovery.py`；`test_migration.py` 的 `invalid` 跨进程恢复 |
| T03 | 通过：显式作者／作品归属局部校验，否定说明不误拒，混合断言不整体豁免 | `tests_python/test_attribution.py`；对应真实作者／缺失案例 |
| T04 | 通过：真实精确取爻、跨卦搜索、指定作者、材料缺失；查询不改变 cast | 最新开发报告四个 `source-*` 案例；缺失作者存在可优化的两次空检索，见下文 |
| T05 | 实现并跑通事实纠正、话题变化、显式重起卦与风险支持；自然语言质量及人工复核待完成，未关闭 | `test_practical_routes.py`；最新开发报告生活链、风险及历史窗口案例 |
| T06 | 通过：无动爻单卡，真实单币取值，旧记录不补造，桌面／手机可用 | UI 三种 fixture × 两种宽度；主 agent 查看手机截图 |
| T07 | 通过：资料与 trace 默认折叠，正文引用展开定位，生活化追问 | 浏览器操作与页面代码复核 |
| T08 | 通过：八类错误的提示／操作；版本不兼容不进行无效原轮重试 | UI 故障 fixture + Python 版本不兼容、新轮及错误脱敏测试 |
| T13 | 工程部分已有真实浏览器、故障和注入记录；依赖 T01/T05 的完整回答质量验收，暂不关闭 | 下列测试和版本明确的原始报告 |

## 验证结果与版本

- 最终代码：`python-graph-v3` / `evidence-agent-python-v4`。
- `npm run test:python`：50 项通过，含 4,096 种爻值组合、API 并发／删除、异常／突然退出／校验耗尽后三种新进程恢复。
- `npm test`：37 项旧 TypeScript 回归通过；`npm run lint`、`npm run build` 通过。
- `npm run knowledge:audit`：1,076 条记录、15 份来源快照、12 条修正、112 条注释回查通过，人工审校仍为 0。
- `.venv/bin/python -m backend.evaluate`：64/64 精确检索契约通过；`pip check` 无依赖冲突。
- 最终版本的开发模拟验证：13 场景通过；真实验证：13 场景机械通过、cast 均保持、无答案修复，25 次请求、107,757 reported tokens。**不等于 13 条语义全部合格。**
- 真实浏览器：两个不同情境各三轮，11 次请求、64,265 tokens；引用定位、刷新、390px、删除业务文件及 SQLite checkpoints/writes 均通过。该记录为 graph v2 / prompt v3；之后 v3/v4 的风险证据隔离和提示词改动由新增回归及最终开发运行验证，未把旧浏览器报告改称最新版本全链验收。
- 检索材料注入：graph v2 / prompt v3 下一个真实样例，2 次请求、7,848 tokens，未执行恶意工具指令或泄漏虚构私有标记；离线权限测试验证未知工具、越界读取及试图修改范围均被拒绝。只证明这些案例，不保证全面防御。
- 本批三轮真实开发运行、浏览器及注入验证共 74 次模型请求、336,520 reported tokens；报告未缺失 usage。各运行设调用／token 上限，不包含之前的迁移冒烟费用，不估算未查询单价的货币金额。

## 真实失败与修复

1. **一般建议被迫引用古文。** 旧校验对首次回答逐段强制引用，现金流建议因此附上不相关经文。现在仅对涉及传统内容的完整回答要求有依据，允许现实段为空引用，同时保留指定爻位、原文与归属校验。最终开发回答的现金流段已无引用；但部分传统联想与建议仍混段，不宣称该类语义问题彻底消失。
2. **爻值 7 被模型称为老阳。** 第一轮真实 `source-missing` 的附加说明出错，程序 cast 一直正确。现在把程序确定的 `lineTypes` 送入上下文，提示词禁止自行猜算和无关展开；后续两轮该失败未复现。只证明当前样例修复，不保证模型永不误述。
3. **技术降级无法重试。** 原来图结束后 API 将无效结果记作 completed，页面没有有效重试动作。现在技术失败单独返回 failed，重试使用既有 prepare → generate 边重新执行生成，保留已经取得的材料，清空本次生成修复状态。跨进程及并发模拟均通过；该故障证据属于可控模拟，不冒充真实模型偶发故障。

checkpoint 状态更新采用已安装版本的 `aupdate_state(..., as_node=...)`，并通过行为测试验证续跑位置；参见 [LangGraph 官方状态更新说明](https://reference.langchain.com/python/langgraph/pregel/main/Pregel/update_state)。没有增加第二套重试编排。

## 保留的问题与下一步

- 最新 `life-initial`、`life-correction` 的第四段仍将传统联想和现实建议合在带引用的一段；T01 保持未完成，后续用固定失败案例验证，避免只凭提示词变强就勾选。
- `source-missing` 为确实未收录的作者进行了两种空查询，并主动给出已明确署名为朱熹的对照；没有冒充焦循，但存在可减少的检索和额外回答。纳入后续规则／Agent 对照，不因这两次不同查询立即引入新路由框架。
- `history-window` 正确拒绝猜测遗失的“17个月”，却向用户提到 `recentHistory` 等内部字段；重起卦及危机回答仍稍冗长。T05 尚需收尾，不增加长期记忆系统。
- 较早真实浏览器中的家庭沟通首段有装饰性引用；真实注入探针也出现过将无动爻推作现实局势的表述。已保留原始记录，后续提示词明确禁止此推断，但未逐一重跑旧 fixture，不能记作全部修复。
- 本批没有实现 B/C/D、40+40、真人盲评、三分钟演示或完成旧后端归档；T09～T12、T14～T15 保持待办。基础交付仍不以正式评测为前置条件。

## 原始记录索引

这些原始报告仍在本地忽略目录，含合成回答和部分文献全文；这里只保存可复核的摘要，不将全文当作可无条件公开分发的资料。T15 另整理公开证据。

- [最终开发运行：graph v3 / prompt v4](../../../evaluation/results/development-real-20261008-084011-8376a4/report.json)
- [最终模拟运行](../../../evaluation/results/development-mock-20261008-083938-9e4aeb/report.json)
- [第一轮开发与爻名错误](../../../evaluation/results/development-real-20261008-083118-891cb6/report.json)
- [第二轮定向复测](../../../evaluation/results/development-real-20261008-083520-369672/report.json)
- [UI 与异常操作](../../../evaluation/results/ui-t06-t08-20261008/report.json)
- [Python API + 模拟模型浏览器](../../../evaluation/results/ui-python-api-20261008/report.json)
- [真实浏览器两组三轮](../../../evaluation/results/t13-live-browser-20261008/report.json)
- [真实注入样例](../../../evaluation/results/python-injection-20261008-v3/report-20261008-083709-97a159.json)

复现入口：`npm run test:python`、`npm run test:browser`、`.venv/bin/python -m evaluation.development_checks`。浏览器默认 API fixture 不等于真实 Python API；真实模型须显式指定相应 live 入口与预算，运行配置见原始记录及 README。
