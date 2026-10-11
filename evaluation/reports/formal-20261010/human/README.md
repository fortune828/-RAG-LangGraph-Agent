# 人工匿名评阅入口

**开始评第一批：** [14份精简阅读包与填写说明](first-batch-20261011/README.md)。包含8个生活场景和2段完整三轮对话，配中文空白评分表。

请只阅读本目录；其他目录含策略解盲与AI评分，提前阅读会影响独立判断。本目录隐藏模型、策略及既有评分，保留完整问题、历史、程序卦象、原文和实际交付状态。所有CSV评分、理由、身份字段均为空，未复制AI分数。

| 范围 | 阅读版 | Markdown | 空白评分表 | 输出数 |
|---|---|---|---|---:|
| 独立测试，建议优先 | [HTML](test/materials.html) | [Markdown](test/materials.md) | [CSV](test/scores.csv) | 160 |
| 真实连续对话 | [HTML](continuous/materials.html) | [Markdown](continuous/materials.md) | [CSV](continuous/scores.csv) | 12 |
| 知识输入消融 | [HTML](rag/materials.html) | [Markdown](rag/materials.md) | [CSV](rag/scores.csv) | 36 |
| 开发输入 | [HTML](development/materials.html) | [Markdown](development/materials.md) | [CSV](development/scores.csv) | 160 |

可自行选择一小批先评，不要求一次看完368份。记录“目录名+answerId”，不同目录编号会重复。每份都按场景排列，保留该轮完整历史；连续轮次不是独立用户。

## 填写方式

1. 先阅读该目录的 `rubric.md`，再看问题、必要历史、实际正文；有传统解释时展开原文依据。
2. 在对应CSV行填写 `target=delivered`、真实 `reviewerId`、`rubricVersion=1.0`；五项各填0、1、2，每项写一句具体理由。
3. 无法判断或不适用的维度留空，填写其 `NAReason`；尚未评阅的整行继续全空。真实0分写0，不能把未评当0。
4. `taskSuccess` 填true、false或unknown并说明；它不是五项之和。诚实表示缺料，不等于完成查证任务。
5. `errorsJson` 和 `claimChecksJson` 可按Rubric填结构化记录；不熟悉JSON时先在 `notes` 写出原句、问题与所对照原文，之后再整理，别为填写格式影响判断。
6. 仅失败提示或无交付时，按Rubric记技术失败。未交付候选可另增 `target=candidate` 行诊断，不能替实际交付评分。完成日期如实填写。

参考原文分两类：生成实际看见的资料，以及 `referenceOnlyEvidence`（仅供评阅查证、模型未见）。后者可以帮助判断知识对错，不能称作答案已检索或已引用。引文存在不证明整段现实建议受到经典支持。

关注是否真正听懂用户、传统解释是否准确、类比是否合理、交流是否自然、建议是否有帮助。不要以字数、段数、引用多或少作为得分标准；不要求不同卦必须给相反建议。
