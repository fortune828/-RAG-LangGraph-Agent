# 文档导航

首次了解项目，请先看[项目首页](../README.md)。这里按阅读目的组织文档；历史版本和原始实验保留，不作为当前系统能力的统一结论。

## 当前设计

| 文档 | 用途 |
|---|---|
| [产品定义](SPEC.md) | 项目目标、产品边界与需求 |
| [系统架构](architecture.md) | Python后端、LangGraph、检索、会话及可靠性设计 |
| [任务状态](tasks.md) | 已完成工作与尚未解决的问题 |
| [验收标准](acceptance.md) | 工程检查与用户体验分别如何验证 |
| [评测计划](evaluation-plan.md) | 对照策略、采样、标注与指标口径 |

## 主要报告

- [最新修复与随机对话实验](reports/context-repair-20261011.md)：当前版本、上下文问题、负面实验和真实回答入口。
- [正式效果评测](reports/final-evaluation-20261010.md)：RAG/Agent对照、成本与覆盖限制。
- [项目展示与面试总结](reports/project-showcase-20261010.md)：能使用的技术表述及数据边界。

## 深入查阅

- [评测程序与原始记录](../evaluation/README.md)：脚本、运行批次、完整回答及复算。
- [知识库来源与发布](../data/knowledge/README.md)：数据来源、覆盖、构建与使用条款。
- [历史归档](archive/README.md)：旧设计、验收、模型实验与此前README。

目录整理于2026-10-15。根目录仅保留项目README；原始JSON/JSONL、评分、源码快照及知识发布未迁移或改写。历史文档内的日期、版本与原路径描述仍按当时语境阅读，Markdown导航链接已调整。
