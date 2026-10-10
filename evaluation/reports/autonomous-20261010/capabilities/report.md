# 模型能力核实 · 2026-10-10

本目录只做能力核实，未修改生产配置。四次短输入探测已完成，不增加更多探测请求。

## 账号实际返回

08:19 UTC 查询官方 `GET /models`，HTTP 200；原始响应见 [models.json](models.json)。账号返回：

| API 名称 | 服务端名称 | context_window | max_output_tokens | effort |
|---|---|---:|---:|---|
| deepseek-flash | DeepSeek-V4.1-Flash | 1,048,576 | 393,216 | low / high / max，默认 high |
| deepseek-v4-pro | DeepSeek-V4-Pro | 1,048,576 | 393,216 | low / high / max，默认 high |

实际请求响应 `model` 与所请求名称一致。响应未给出权重快照哈希；名称与 `system_fingerprint` 可记录，但不代表能够锁定未来服务端权重。[模型接口说明](https://api-docs.deepseek.com/api/list-models/)

## 参数能力与调用注意点

- `thinking.type` 支持 enabled/disabled；思考档位正式支持 low/high/max。medium 映射为 high，不能当成独立档位。Thinking 默认开启。思考模式中 temperature 不生效；top_p 的有效区间为 0.95–1.0，非思考模式忽略 top_p。[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)
- `max_tokens` 同时限制推理和正文，最大 393,216；省略时非思考默认 8K、思考默认 64K，max 档默认 128K。这些是接口能力，不是本项目推荐预算。JSON 输出支持 `json_object`，需要提示词明确要求 JSON；没有已核实的最终回答 `json_schema` 严格模式。`tool_choice=required` 或指定工具不适用于思考模式；工具参数仍须服务端验证。[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)
- 官方明确提及 JSON 模式偶尔可能给空 content；仍须独立处理 `finish_reason=length`、空正文与不合法结构，而不是仅凭 HTTP 200 判断成功。[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)
- 带 tools 的思考模式请求需要回传先前 assistant 的 `reasoning_content`，否则可能 HTTP 400。当前图追加完整模型消息，符合该要求；公开实验记录仅保存该字段长度和哈希。非思考模式无需推理文本。工具 strict 为 beta 功能，不能直接把它当作现有普通端点的默认能力。[工具调用](https://api-docs.deepseek.com/guides/tool_calls/)

## 四次有界实测

完全相同的算术输入与 JSON 协议，`max_tokens=1024`，串行执行；未发送 temperature/top_p/seed。问题仅为 17 + 28。

| 模型 | Thinking | HTTP / finish | 输入 / 输出 / 总 tokens | 推理 tokens | 延迟 ms |
|---|---|---|---|---|---:|
| Flash | disabled | 200 / stop | 49 / 6 / 55 | 未返回 | 751 |
| Flash | enabled + low | 200 / stop | 74 / 40 / 114 | 34 | 870 |
| Pro | disabled | 200 / stop | 46 / 6 / 52 | 未返回 | 951 |
| Pro | enabled + low | 200 / stop | 46 / 75 / 121 | 68 | 2,412 |

四份可见答案均为合法 JSON，answer=45。合计 4 次生成、342 reported tokens，其中输入 215、输出 127。非思考响应没有 reasoning usage 明细，原始数据保持缺失，没有补 0。缓存命中均由接口明确返回 0。

**这只证明本账号能成功调用两模型的两种配置，不是产品任务质量或典型延迟的结论。** high/max 与大 token 窗口本次未额外探测；supported_levels/max_output 是账号元数据及官方文档证据。没有探测强制工具调用、beta strict 或窗口极限。

## 当前价格与估算方法

美元 / 百万 tokens；顺序为缓存命中输入、未命中输入、输出：

| 模型 | 非高峰 | 高峰 |
|---|---|---|
| Flash | 0.003 / 0.15 / 0.60 | 0.006 / 0.30 / 1.20 |
| Pro | 0.022 / 0.66 / 1.98 | 0.044 / 1.32 / 3.96 |

官方将周一至周五 UTC 01:00–04:00、06:00–10:00 列为高峰，排除中国法定节假日；周末全天非高峰。2026-10-10 为周六，本轮使用非高峰表。价格表来自当日官方页面的搜索索引结果，直接打开该页多次超时，因此未声称获得当日原站完整快照。[官方价格](https://api-docs.deepseek.com/quick_start/pricing/)

机器可读版本见 [price_table.json](price_table.json)。用接口返回的 cache hit、cache miss、completion 分别计费，推理 tokens 已含在 completion 中，不重复相加。四次探测估算 **$0.00026715**。这不是账户账单扣款核对，不包含免费额度或其他优惠；缺失用量时不得给出虚假精确费用。

本轮检索未发现 10 月另行促销，不能据此断言任何账户都没有赠送额度。9 月发布新闻曾宣布 Pro 将转到 Flash，但后续官方更新日志与中文价格页明确改为继续提供 Pro，计费不变；本轮账号 `/models` 也独立列出 Pro。因此实验保留两个模型名称，不沿用新闻中已变更的下线计划。[更新日志](https://api-docs.deepseek.com/updates/)、[中文价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

缓存默认启用、按已落盘前缀的完整匹配命中，构建需要时间且不保证命中；不应因输入重复就自行假定享受缓存价。使用每次响应的实际 cache hit/miss 计数，交错运行并保留这些字段。没有采用历史新闻中的旧版固定 64-token 单元来推算当前命中。[当前缓存规则](https://api-docs.deepseek.com/guides/kv_cache/)

## 对主实验的建议

先比较 Flash disabled、Flash low、现有 Flash high，以及 Pro disabled；如 Pro 非思考表现好，再有针对性比较 Pro low。所有组仍使用相同任务输入、输出协议和校验；预先设统一的调用与 token 总预算。无需仅因为官方上限较大就扩大预算，更不应先采用多模型路由。

应单独观察：正文是否被推理挤占、JSON 空正文、修复调用、补检索循环和返回错误。本目录没有对这些产品级问题下质量结论。

复现：仓库根目录运行 `.venv/bin/python evaluation/reports/autonomous-20261010/capabilities/probe.py --output evaluation/results/capabilities-rerun`。脚本需 `.env.local` 中的 API key，会产生四次付费生成；输出目录已有 JSON 时拒绝覆盖。所有记录均为合成算术输入，未读取生产聊天。
