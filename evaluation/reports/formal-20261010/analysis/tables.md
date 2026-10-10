# 可重算指标表

机械完成不是语义成功；AI 评分不是真人验收。失败保留在运行分母，五维无答案为 N/A。

| 阶段 / 策略 | 完成 / N | 调用 | Tokens | P50 / P95 秒 | Repair 成功 / 触发 | 估算 USD |
|---|---:|---:|---:|---:|---:|---:|
| continuous / development / H | 6/6 | 10 | 40198 | 16.61 / 25.19 | 0/0 | 0.014463 |
| continuous / independent_test / H | 6/6 | 10 | 42052 | 19.02 / 20.15 | 0/0 | 0.015797 |
| development / development / B | 37/40 | 45 | 160934 | 15.93 / 25.28 | 2/5 | 0.079547 |
| development / development / C | 40/40 | 43 | 174334 | 16.77 / 28.68 | 3/3 | 0.088377 |
| development / development / D | 39/40 | 69 | 256085 | 17.57 / 30.73 | 4/5 | 0.093589 |
| development / development / H | 39/40 | 71 | 273155 | 18.61 / 29.15 | 5/6 | 0.100621 |
| rag / development / classic | 3/6 | 6 | 13268 | 8.56 / 11.84 | 0/0 | 0.005929 |
| rag / development / full | 4/6 | 6 | 27472 | 15.74 / 25.16 | 0/0 | 0.013453 |
| rag / development / memory | 6/6 | 6 | 14640 | 11.54 / 16.73 | 0/0 | 0.007157 |
| rag / independent_test / classic | 3/6 | 6 | 16511 | 9.59 / 17.18 | 0/0 | 0.007851 |
| rag / independent_test / full | 4/6 | 6 | 29665 | 20.49 / 22.12 | 0/0 | 0.014647 |
| rag / independent_test / memory | 6/6 | 6 | 16288 | 9.64 / 24.88 | 0/0 | 0.008118 |
| smoke / smoke_new / H | 1/1 | 1 | 9039 | 33.89 / 33.89 | 0/0 | 0.004825 |
| smoke / smoke_seen / H | 3/3 | 5 | 14555 | 12.34 / 15.38 | 0/0 | 0.004042 |
| test / independent_test / B | 39/40 | 45 | 188115 | 17.85 / 30.57 | 4/5 | 0.095215 |
| test / independent_test / C | 39/40 | 43 | 176965 | 18.35 / 25.13 | 2/3 | 0.089689 |
| test / independent_test / D | 37/40 | 70 | 279784 | 18.09 / 32.65 | 4/7 | 0.107075 |
| test / independent_test / H | 38/40 | 66 | 250922 | 19.31 / 28.13 | 3/5 | 0.096354 |

## 独立 AI 首评

顺序：理解 / 知识 / 关联 / 交流 / 帮助；每项 0–2，仅对有分值的回答取均值；N/A 数见 quality.json。

| 阶段 / 策略 / 场景 | N | 确认完成 / N | 五维均分 |
|---|---:|---:|---|
| continuous / development / H / continuous | 6 | 6/6 | 1.83 / 2.00 / 1.33 / 1.83 / 1.83 |
| continuous / independent_test / H / continuous | 6 | 6/6 | 1.50 / 1.67 / 1.33 / 1.33 / 1.33 |
| development / development / H / boundary | 4 | 4/4 | 2.00 / 2.00 / 1.00 / 1.75 / 2.00 |
| development / development / H / knowledge | 16 | 12/16 | 2.00 / 1.92 / 2.00 / 1.93 / 1.67 |
| development / development / H / life | 20 | 20/20 | 1.60 / 1.50 / 1.05 / 1.75 / 1.85 |
| rag / development / classic / knowledge | 6 | 0/6 | 2.00 / 1.50 / 1.50 / 1.67 / 1.00 |
| rag / development / full / knowledge | 6 | 3/6 | 2.00 / 2.00 / 2.00 / 2.00 / 1.75 |
| rag / development / memory / knowledge | 6 | 3/6 | 2.00 / 1.33 / 2.00 / 2.00 / 1.50 |
| rag / independent_test / classic / knowledge | 6 | 0/6 | 1.67 / 2.00 / N/A / 1.67 / 0.67 |
| rag / independent_test / full / knowledge | 6 | 4/6 | 2.00 / 2.00 / 2.00 / 2.00 / 2.00 |
| rag / independent_test / memory / knowledge | 6 | 1/6 | 2.00 / 0.83 / 1.67 / 2.00 / 1.00 |
| test / independent_test / B / boundary | 4 | 4/4 | 1.50 / 1.50 / 1.25 / 1.75 / 2.00 |
| test / independent_test / B / knowledge | 16 | 10/16 | 1.87 / 1.62 / 1.67 / 1.80 / 1.47 |
| test / independent_test / B / life | 20 | 20/20 | 1.60 / 1.55 / 1.10 / 1.85 / 1.85 |
| test / independent_test / C / boundary | 4 | 2/4 | 1.67 / 1.67 / 1.33 / 1.33 / 1.67 |
| test / independent_test / C / knowledge | 16 | 12/16 | 2.00 / 1.71 / 2.00 / 1.94 / 1.69 |
| test / independent_test / C / life | 20 | 20/20 | 1.50 / 1.35 / 1.05 / 1.70 / 1.80 |
| test / independent_test / D / boundary | 4 | 3/4 | 1.75 / 2.00 / 1.50 / 1.75 / 1.75 |
| test / independent_test / D / knowledge | 16 | 10/16 | 2.00 / 1.92 / 2.00 / 2.00 / 1.69 |
| test / independent_test / D / life | 20 | 19/20 | 1.45 / 1.45 / 1.05 / 1.65 / 1.80 |
| test / independent_test / H / boundary | 4 | 3/4 | 1.75 / 2.00 / 1.50 / 1.75 / 1.75 |
| test / independent_test / H / knowledge | 16 | 12/16 | 2.00 / 1.85 / 2.00 / 2.00 / 1.80 |
| test / independent_test / H / life | 20 | 19/20 | 1.63 / 1.42 / 1.16 / 1.63 / 1.79 |
