# C/W1 与 D/W2 开发配对结果

Mechanical checks and recorded usage only. Semantic scores pending; no gold-based retrieval metrics or price estimates. Unresolved requests have unknown execution and usage; modelCalls is the confirmed count, modelCallsTotal is null if unresolved.

| 组别 | 机械完成 / 已执行 | 模型调用 | 工具成功 / 空 / 拒绝 | reported tokens | unknown calls | P50 ms | P95 ms |
|---|---:|---:|---|---:|---:|---:|---:|
| C | 14/14 | 14 | 4/1/0 | 33145 | 0 | 3752.5 | 9117 |
| D | 14/14 | 27 | 9/0/1 | 104344 | 0 | 5146.0 | 11831 |

逐题和分场景数据：comparison.csv / metrics.json；输入：inputs.json；逐次请求、工具、验证及节点更新：traces/。

不以机械完成代表任务语义成功。成本金额未估算；未知用量保留 null/unknown。分位数是小样本描述，不代表生产 SLA。
