# 王弼注全量交叉核对（2026-09-26）

范围为 `knowledge-v3-20260926` 中全部 47 条王弼爻注（乾、坤、屯、蒙、谦、豫、坎、恒）。此次只新增审查资料，没有更改 release、active 或应用代码。

## 结果与口径

- 17 条：基本规范化（繁简、标点）后与 Kanripo 对应注文一致。
- 16 条：只见字形异体，如濳/潜、隂/陰、覩/睹、𫉬/獲；不认定为错误。
- 1 条：谦九三“上承下接/綏”为有明确校勘记载的版本异文，保留。
- 7 条：当前录文存在有多渠道、影印位置支持的脱衍误字，详见 `corrections.json`。
- 6 条：存在其他用字分歧，暂留待版本复核，不自动改：乾九四豫/與、坤上六已/巳、屯六四巳/已、屯九五間/問、豫六五与上六巳/已。

全部 47 条已用 Kanripo 录文作逐段比较，并增加易学网对应卦页的逐条文本对照；七项修正建议另直接视觉检查影印图像。47 条均保持作者/经文爻位对齐，未发现把孔疏或陆氏音义错归为王弼的段落。本报告不声称进行了 47 条逐字影印校勘，也不代表人工领域专家审定。

## 来源与独立性

1. 当前见证：项目保存的维基文库固定修订原始 wiki，逐条 URL 和 rawLocator 均留存于 `records.jsonl`。
2. 独立对照录文：[Kanripo《周易註疏》KR1a0007](https://github.com/kanripo/KR1a0007)，文渊阁四库全书本，固定 commit `0fa05af11e9229b6cc6febe312d541fc6d4ec3bf`；主要使用卷一、二、四、五、六。GitHub 页面与 raw 链接属于同一份数据，不计为两个信源。
3. 额外对照：[易学网《周易正义》](https://www.eee-learning.com/article/643) 八卦文本及其公开古籍影印。网页标注“已完成校对”是网站自身说明，不能替代本项目人工核对。该网站录文与维基录文之间的历史依赖尚未完全确认，因此不单凭三个网页“投票”；重要差异均回查实际影印字形。所附影印刊本书目信息仍需进一步确认，不能擅自称为阮刻或宋本。
4. 异文专项：[识典《周易注疏卷四考证》](https://www.shidianguji.com/book/SK0006/chapter/1l9rdovxh6n7z) 明记“綏本或作接”，据此将谦九三归为版本异文。识典 SK0006 为另一托管渠道的四库资料，不当作独立第三个古本。

七项待应用修正：

| 卦爻 | 当前 | 建议 | 直接检查的影印图 |
|---|---|---|---|
| 屯六二 | 故屯邅。「屯」時方屯難 | 故屯邅也。時方屯難 | [136.gif](https://www.eee-learning.com/eeebook/zenyi/136.gif) 左部注文，六二爻辞下 |
| 蒙初六 | 刑不可長。 | 刑不可長也。 | [146.gif](https://www.eee-learning.com/eeebook/zenyi/146.gif) 右部注文末 |
| 蒙九二 | 故「包蒙吉」也 | 故曰「包蒙吉」也 | [147.gif](https://www.eee-learning.com/eeebook/zenyi/147.gif) 右部九二注文 |
| 蒙上九 | 童蒙原發 | 童蒙願發 | [150.gif](https://www.eee-learning.com/eeebook/zenyi/150.gif) 左部上九注文 |
| 谦上六 | 可以邑一國 | 可以征邑國 | [249.gif](https://www.eee-learning.com/eeebook/zenyi/249.gif) 中部上六注文 |
| 坎六三 | 「枕」者，枕枝而不安之謂也 | 「枕」者，枝而不安之謂也 | [361.gif](https://www.eee-learning.com/eeebook/zenyi/361.gif) 右起首列“枕者枝而不安…” |
| 恒九三 | 上不至尊，下不至卑 | 上不全尊，下不全卑 | [389.gif](https://www.eee-learning.com/eeebook/zenyi/389.gif) 右起首列至次列 |

所有修正均是对当前转录文本的局部审查结论，不宣称消灭了古籍全部版本差异；尚需人工校勘。修正时保留原文、旧版本、before/after、依据和 machine_assisted 状态，不能修改后仍声称“与原维基固定修订完全一致”。

## 复查方式

- `records.jsonl`：47 条逐条状态、两路比对文本、差异、来源、作者归属和机器辅助标签。
- `corrections.json`：7 条局部建议，`applied: false`，含完整改前/改后文本。
- `snapshots.json`：已获取快照及 SHA-256，公开资料只作本地审查参考，没有进入运行时知识库。
- `compare.py`：从本地 Kanripo 快照抽取注段并全量对齐；不请求外部模型。
- `finalize.py`：生成审查清单，保留每一处原始差异；明确记录七项影印复核结论。

执行：在项目根目录先运行 `python data/knowledge/review/crosscheck-20260926/wang-bi/compare.py`，再运行同目录的 `finalize.py`。它们只写本审查目录。

方法限制：最终对齐先限定实际 JUAN 卷名，再限定卦符区间、爻名与经文前缀，提取该爻后的直接注文；不再用全库相似度选段。47 条均已重跑，正文对照及来源文件均未改变，结果见 `alignment_validation.json`。每条记录保存原始文件行号、页码和 Unicode 字符偏移。相似度仅用于报告文字差异，不是“内容正确率”。基本规范化不覆盖所有古字，保留异体差异以供复查。所有记录均为 `machine_assisted`，`humanReviewed: false`。网络失败包括 CText 403、HKSH TLS 证书错误、识典直接 requests 444；没有把这些未读取页面作为正误证据。识典可通过浏览检索工具实际读取。
