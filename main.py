import asyncio
import hashlib
import json
from collections.abc import AsyncGenerator
from datetime import datetime

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.api.star import Context, Star

from .ezbookkeeping import (
    TRANSACTION_TYPES,
    BookkeepingError,
    EzBookkeepingClient,
    amount_units,
    find_item,
    format_amount,
)
from .receipt import render_receipt_png

KIND_NAMES = {1: "余额调整", 2: "收入", 3: "支出", 4: "转账"}
ACCESS_DENIED = "当前会话无权访问账本，请在授权的私聊中使用。"


class EzBookkeepingPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.write_lock = asyncio.Lock()
        # (umo, transaction_id) of already-sent receipts, to avoid duplicate cards.
        self._receipts_sent: dict[tuple[str, str], bool] = {}

    def _claim_receipt(self, event: AstrMessageEvent, transaction_id: str) -> bool:
        key = (event.unified_msg_origin, transaction_id)
        if key in self._receipts_sent:
            return False
        self._receipts_sent[key] = True
        if len(self._receipts_sent) > 500:
            self._receipts_sent.pop(next(iter(self._receipts_sent)))
        return True

    async def _receipt_png(self, **kwargs) -> bytes | None:
        try:
            return await asyncio.to_thread(render_receipt_png, **kwargs)
        except Exception:  # noqa: BLE001 - a failed image must never break bookkeeping
            logger.warning("生成记账回执图片失败，已跳过图片。", exc_info=True)
            return None

    def _client(self, event: AstrMessageEvent) -> EzBookkeepingClient:
        allowed = self.config.get("allowed_private_sessions", [])
        if not event.is_private_chat() or not (
            event.unified_msg_origin in allowed if allowed else event.is_admin()
        ):
            raise BookkeepingError(ACCESS_DENIED)
        return EzBookkeepingClient(
            self.config.get("base_url", ""),
            self.config.get("api_token", ""),
            self.config.get("timezone", "Asia/Shanghai"),
        )

    @filter.command("ezbookkeeping_status")
    async def connection_status(self, event: AstrMessageEvent):
        """检查 ezBookkeeping 是否可访问。"""
        try:
            client = self._client(event)
            accounts = await client.accounts()
            reply = f"已连接 ezBookkeeping，可用账户 {len(accounts)} 个。"
        except BookkeepingError as error:
            reply = str(error)
        yield event.plain_result(reply)

    @filter.llm_tool(name="ezbookkeeping_options")
    async def options(self, event: AstrMessageEvent, kind: str) -> str:
        """当用户请求 ezBookkeeping 记账或查看可用账户与分类时使用。新增交易前先调用本工具查找真实 ID，并使用返回的本地日期解析今天、昨天等相对日期。

        Args:
            kind(string): 交易类型，income 表示收入，expense 表示支出，transfer 表示转账。
        """
        try:
            client = self._client(event)
            if kind not in TRANSACTION_TYPES:
                raise BookkeepingError("交易类型请选 income、expense 或 transfer。")
            accounts, categories = await asyncio.gather(
                client.accounts(), client.categories(kind)
            )
            return json.dumps(
                {
                    "today": datetime.now(client.zone).date().isoformat(),
                    "timezone": client.zone.key,
                    "accounts": [item.describe() for item in accounts],
                    "categories": [item.describe() for item in categories],
                },
                ensure_ascii=False,
            )
        except BookkeepingError as error:
            return str(error)

    @filter.llm_tool(name="ezbookkeeping_add_transaction")
    async def add_transaction(
        self,
        event: AstrMessageEvent,
        kind: str,
        amount: str,
        account_id: str,
        category_id: str,
        comment: str = "",
        date: str = "",
        currency: str = "",
        destination_account_id: str = "",
        destination_amount: str = "",
        destination_currency: str = "",
    ) -> AsyncGenerator[object, None]:
        """在 ezBookkeeping 新增一条收入、支出或转账。仅在用户表达实际记账意图时调用。账单描述、假设、讨论和引用均不能视为记账授权。先调用 ezbookkeeping_options 获取真实 ID。金额、账户或分类不明确时先询问。只有工具返回「已记账」才报告写入成功，写入结果未确认时先查流水。

        Args:
            kind(string): income 表示收入，expense 表示支出，transfer 表示转账。
            amount(string): 用户给出的正数金额，使用十进制字符串，例如 25.50。
            account_id(string): 现有账户的 ID。收入为收款账户，支出为付款账户，转账为转出账户。
            category_id(string): 与交易类型匹配的现有二级分类 ID。
            comment(string): 原交易说明，例如午餐。无说明时传空字符串。
            date(string): 交易日期 YYYY-MM-DD。未指定时传空字符串。
            currency(string): 用户明确给出的原币种 ISO 代码，例如 CNY。未指定时传空字符串，由账户币种决定。
            destination_account_id(string): 转账的转入账户 ID。其他类型传空字符串。
            destination_amount(string): 转入金额。跨币种时必须由用户提供。同币种可传空字符串，沿用转出金额。
            destination_currency(string): 用户明确给出的转入币种 ISO 代码。未指定时传空字符串。
        """
        try:
            client = self._client(event)
            if kind not in TRANSACTION_TYPES:
                raise BookkeepingError("交易类型请选 income、expense 或 transfer。")
            source_amount = amount_units(amount)
            when = client.transaction_time(date)
            if len(comment) > 255:
                raise BookkeepingError("交易说明最多 255 个字符。")
            accounts, categories = await asyncio.gather(
                client.accounts(), client.categories(kind)
            )
            account = find_item(accounts, account_id, "账户")
            category = find_item(categories, category_id, "二级分类")
            if currency and currency.strip().upper() != account.currency:
                raise BookkeepingError(
                    "交易币种与所选账户的币种不一致，请核对后再记账。"
                )
            payload = {
                "type": TRANSACTION_TYPES[kind],
                "sourceAccountId": account.id,
                "sourceAmount": source_amount,
                "categoryId": category.id,
                "comment": comment,
            }
            detail = f"{account.name} {format_amount(source_amount)} {account.currency}"
            if kind == "transfer":
                target = find_item(accounts, destination_account_id, "转入账户")
                if target.id == account.id:
                    raise BookkeepingError("转出账户与转入账户必须不同。")
                if (
                    destination_currency
                    and destination_currency.strip().upper() != target.currency
                ):
                    raise BookkeepingError(
                        "转入币种与转入账户的币种不一致，请核对后再记账。"
                    )
                if not destination_amount and target.currency != account.currency:
                    raise BookkeepingError("跨币种转账需要明确的转入金额。")
                target_amount = (
                    amount_units(destination_amount)
                    if destination_amount
                    else source_amount
                )
                if (
                    target.currency == account.currency
                    and target_amount != source_amount
                ):
                    raise BookkeepingError("同币种转账的转出与转入金额必须相同。")
                payload.update(
                    destinationAccountId=target.id, destinationAmount=target_amount
                )
                detail += (
                    f" → {target.name} {format_amount(target_amount)} {target.currency}"
                )
            elif destination_account_id or destination_amount or destination_currency:
                raise BookkeepingError("只有转账可以填写转入参数。")

            # Keep the same submission key when a provider repeats an identical tool call.
            message_id = event.message_obj.message_id
            if message_id:
                key = json.dumps(
                    [
                        event.unified_msg_origin,
                        event.get_sender_id(),
                        str(message_id),
                        when.date().isoformat(),
                        payload,
                    ],
                    sort_keys=True,
                )
                payload["clientSessionId"] = hashlib.sha256(key.encode()).hexdigest()
            payload.update(time=int(when.timestamp()), utcOffset=client.offset(when))
            async with self.write_lock:
                transaction_id = await client.add(payload)

            reply = (
                f"已记账（编号 {transaction_id}）\n"
                f"{when:%Y-%m-%d} {KIND_NAMES[TRANSACTION_TYPES[kind]]} · {category.name} · {detail}"
            )
            if kind == "transfer":
                amount_line = (
                    f"{format_amount(source_amount)} {account.currency} → "
                    f"{format_amount(target_amount)} {target.currency}"
                )
                rows = [
                    ("转出账户", account.name),
                    ("转入账户", target.name),
                    ("分类", category.name),
                ]
            else:
                sign = "+" if kind == "income" else "-"
                amount_line = f"{sign}{format_amount(source_amount)} {account.currency}"
                rows = [("账户", account.name), ("分类", category.name)]

            if self.config.get("send_receipt_image", True) and self._claim_receipt(
                event, transaction_id
            ):
                png = await self._receipt_png(
                    kind=KIND_NAMES[TRANSACTION_TYPES[kind]],
                    amount=amount_line,
                    rows=rows,
                    note=comment,
                    transaction_id=transaction_id,
                    date_text=f"{when:%Y-%m-%d}",
                )
                if png:
                    yield event.chain_result([Image.fromBytes(png)])
                    # The card was sent as a direct tool message; clear the
                    # result so the final reply stage does not send it again.
                    event.clear_result()
            yield reply
        except BookkeepingError as error:
            yield str(error)

    @filter.llm_tool(name="ezbookkeeping_recent_transactions")
    async def recent_transactions(self, event: AstrMessageEvent, count: int = 5) -> str:
        """仅在用户要求查询自己的 ezBookkeeping 最近流水时使用。

        Args:
            count(number): 返回条数，1 到 20 的整数，默认 5。
        """
        try:
            client = self._client(event)
            if (
                type(count) not in (int, float)
                or not 1 <= count <= 20
                or int(count) != count
            ):
                raise BookkeepingError("查询条数必须是 1 到 20 之间的整数。")
            items = await client.recent(int(count))
            if not items:
                return "未找到流水。"
            lines = []
            for item in items:
                when = datetime.fromtimestamp(item["time"], client.zone)
                kind = KIND_NAMES.get(item["type"], "交易")
                account = item.get("sourceAccount") or {}
                money = "金额已隐藏"
                if not item.get("hideAmount"):
                    money = f"{format_amount(item['sourceAmount'])} {account.get('currency', '')}".strip()
                    if item["type"] == TRANSACTION_TYPES["transfer"]:
                        target = item.get("destinationAccount") or {}
                        money += f" → {target.get('name', '')} {format_amount(item['destinationAmount'])} {target.get('currency', '')}".rstrip()
                comment = item.get("comment", "")
                lines.append(
                    f"{when:%Y-%m-%d} {kind} · {account.get('name', '')} · {money}"
                    + (
                        f" · {comment}"
                        if comment and not item.get("hideAmount")
                        else ""
                    )
                )
            return "\n".join(lines)
        except BookkeepingError as error:
            return str(error)
        except (KeyError, TypeError, ValueError, OverflowError, OSError):
            return "ezBookkeeping 返回了无法识别的流水数据。"
