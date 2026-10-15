# 评测代码与实验数据

效果结论集中在[主要报告](../docs/README.md)，本目录保留执行器、输入、原始输出和评分，便于复现与核查。

## 从哪里开始

| 目的 | 入口 |
|---|---|
| 阅读最新完整对话或人工评分 | [随机对话记录](reports/context-20261011/README.md) |
| 检查正式RAG/Agent对照 | [正式评测记录](reports/formal-20261010/README.md) |
| 查看模型、Thinking和路由实验 | [自主实验记录](reports/autonomous-20261010/README.md) |
| 查看语义优化及失败 | [语义实验记录](reports/semantic-20261010/README.md) |
| 查旧阶段报告 | [历史实验文档](../docs/archive/experiments/) |

## 文件组织

- 根目录 `.py`：沿用现有模块路径的执行、导出与统计脚本。
- `formal/`、`phase1/`、`baselines/` 等：各阶段的配置、案例或基线材料。
- `reports/`：按实验批次保存manifest、输入、工具轨迹、模型输出、评分和源码快照。
- `results/`：被Git忽略的本地运行输出，不是公开复现材料的唯一存放位置。

原始文件较多是为了保留逐题结果和失败证据。源码快照用于核对历史版本，不属于当前生产后端；不要把不同版本和不同样本的数字直接合并。

## 无付费调用的复算

在仓库根目录执行：

```bash
.venv/bin/python -m evaluation.context_results
```

其他批次的复现命令见各记录目录README。真实模型运行可能产生费用，需遵守对应预算；人工表与AI评分分开，不自动代填。
