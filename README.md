# ezBookkeeping 记账 for AstrBot

在 AstrBot 私聊里用自然语言新增收入、支出和转账，或查询最近流水。插件使用 AstrBot 当前会话的模型识别记账意图，通过 ezBookkeeping 的 HTTP API 访问一个账本。

## 安装与配置

1. 使用 AstrBot 4.16 或更新的 4.x 版本，并为会话启用支持工具调用的模型和 AstrBot 内置 Agent 执行器。若人格限制了工具，请允许 `ezbookkeeping_options`、`ezbookkeeping_add_transaction` 和 `ezbookkeeping_recent_transactions`。
2. 在 AstrBot WebUI 的插件页通过仓库地址安装。也可以把本仓库放进 AstrBot 的 `data/plugins/astrbot_plugin_ezbookkeeping`，安装依赖后重载插件。
3. 在 ezBookkeeping 服务端启用 `[security]` 下的 `enable_api_token`，或设置环境变量 `EBK_SECURITY_ENABLE_API_TOKEN=true`。在 ezBookkeeping 网页端打开「用户设置 → 安全」并生成 API Token。
4. 在 AstrBot WebUI 打开「插件 → ezBookkeeping 记账 → 配置」，填写下表中的设置并保存。必要时重载插件。支持 `secret` 配置项的 AstrBot WebUI 会遮罩 Token。AstrBot 4.16 的界面以普通文本显示，插件配置中会保存原始值。
5. 在授权的私聊发送 `/ezbookkeeping_status`。连接成功时，插件会返回可用账户数。

| 设置 | 填写方式 |
| --- | --- |
| ezBookkeeping 服务地址 | 填写 AstrBot 运行环境可访问的实例根地址，例如 `https://books.example.com`。反代在子路径时保留路径，例如 `https://example.com/books`。 |
| API Token | 填写 ezBookkeeping 生成的令牌。 |
| 记账时区 | IANA 时区名称，默认 `Asia/Shanghai`。 |
| 允许访问账本的私聊会话 | 留空时仅 AstrBot 管理员可在私聊使用。设置后仅列表里的私聊会话可用。所有授权会话访问同一个 Token 对应的账本。 |
| 记账成功后发送回执图片 | 默认开启。开启时每笔记账成功会额外发送一张账本风格回执图片；关闭则只返回文字回执。 |

## 使用示例

按 AstrBot 平台原有的唤醒规则与机器人对话，账户和分类名称按你的账本替换。

- 「帮我记一笔支出，今天微信钱包付了 25.50 元午餐，分类选餐饮里的午餐。」
- 「把昨天发到工资卡的 8500 元记成工资收入。」
- 「记一笔从储蓄卡转 200 元到微信钱包的转账。」
- 「查一下我最近五笔流水。」

在 ezBookkeeping 内先创建可用账户及对应交易类型的二级分类。账户或分类不明确时，模型会询问。跨币种转账需要给出转出和转入的实际金额。

可以说「补记昨天晚上 8 点半微信钱包付的 25.50 元晚餐」。交易按插件配置的时区记录，回执和最近流水显示日期与时分秒。只指定时间时使用今天。未指定时间时，今天的交易使用当前时间，其他日期使用当地中午。

模型识别到明确的记账请求并得到必要信息后会直接提交。新增交易成功会返回交易编号。写入结果无法确认时，先查流水再重试。当前版本支持查询最近 1 至 20 笔流水。

## 记账回执图片

记账成功后，如果开启了「记账成功后发送回执图片」，插件会生成一张纸质账本风格的回执图，包含交易类型、日期与时间、突出显示的金额、账户与分类、备注和交易编号。收入用墨绿、支出用砖红、转账用深蓝，跨币种转账会分别显示两侧账户与金额。图片在本地用 Pillow 渲染，不联网、不经过模型。

- 依赖：`Pillow>=10`，由插件依赖列表安装。字体随插件打包在 `assets/fonts/`（文泉驿微米黑，Apache-2.0），因此 Docker 环境无需额外系统字体。
- 图片发送失败或渲染异常时，插件仍会正常返回文字回执，不会重复记账。
- 同一笔记账在模型重复调用工具时只发一张回执，按交易编号去重。
- 用 `send_receipt_image` 开关可关闭图片，只保留文字回执。

通过模型查询流水时，查询结果会传给该会话的模型提供商。插件不会把 ezBookkeeping API Token 传给模型。

## 参考

- [AstrBot 插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [ezBookkeeping HTTP API](https://ezbookkeeping.mayswind.net/httpapi/)
