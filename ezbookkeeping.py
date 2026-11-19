"""Async client for ezBookkeeping's documented v1 HTTP API."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

TRANSACTION_TYPES = {"income": 2, "expense": 3, "transfer": 4}


class BookkeepingError(Exception):
    """An error safe to show in chat."""


class WriteOutcomeUnknown(BookkeepingError):
    def __init__(self, reason: str):
        super().__init__("写入结果未能确认，请先查询流水核实后再提交。")
        self.reason = reason


@dataclass(frozen=True)
class LedgerItem:
    id: str
    name: str
    currency: str = ""

    def describe(self) -> str:
        currency = f" [{self.currency}]" if self.currency else ""
        return f"{self.name}{currency}（ID: {self.id}）"


def amount_units(value: str) -> int:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{1,13}(?:\.[0-9]{1,2})?", value.strip()
    ):
        raise BookkeepingError("金额请填写正数，最多两位小数，例如 25.50。")
    amount = int(Decimal(value.strip()) * 100)
    if amount <= 0:
        raise BookkeepingError("金额必须大于 0。")
    return amount


def format_amount(value: int) -> str:
    return f"{Decimal(value) / 100:.2f}"


def find_item(items: list[LedgerItem], item_id: str, label: str) -> LedgerItem:
    for item in items:
        if item.id == item_id:
            return item
    raise BookkeepingError(f"请选择账本中可用的{label}，请先查询账户和分类。")


class EzBookkeepingClient:
    def __init__(self, base_url: str, token: str, timezone: str):
        base_url = base_url.strip().rstrip("/")
        token = token.strip()
        if not base_url or not token:
            raise BookkeepingError(
                "请先在插件设置中填写 ezBookkeeping 地址和 API Token。"
            )
        try:
            url = urlsplit(base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username is not None
                or "?" in base_url
                or "#" in base_url
                or url.path.rstrip("/").endswith("/api/v1")
                or any(c.isspace() for c in base_url)
            ):
                raise ValueError
            httpx.URL(base_url)
        except (ValueError, httpx.InvalidURL):
            raise BookkeepingError("请填写有效的 ezBookkeeping 实例根地址。") from None
        if not token.isascii() or not all(33 <= ord(c) <= 126 for c in token):
            raise BookkeepingError("API Token 格式无效，请在插件设置中检查。")
        try:
            self.zone = ZoneInfo(timezone.strip())
        except (ValueError, ZoneInfoNotFoundError):
            raise BookkeepingError("记账时区无效，请填写 IANA 时区名称。") from None
        self.base_url = f"{base_url}/api/v1/"
        self.token = token

    async def request(self, method: str, endpoint: str, **kwargs):
        writing = method == "POST"
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "X-Timezone-Name": self.zone.key,
                    "X-Timezone-Offset": str(self.offset(datetime.now(self.zone))),
                },
                timeout=10.0,
                follow_redirects=False,
            ) as client:
                response = await client.request(method, endpoint, **kwargs)
        except httpx.RequestError as error:
            if writing:
                raise WriteOutcomeUnknown(type(error).__name__) from None
            raise BookkeepingError(
                "无法连接 ezBookkeeping，请检查服务地址、网络和证书。"
            ) from None

        if response.status_code in (401, 403):
            raise BookkeepingError(
                "ezBookkeeping 鉴权失败，请检查 API Token 和服务配置。"
            )
        if response.status_code >= 500 and writing:
            raise WriteOutcomeUnknown(f"HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict) and payload.get("success") is False:
            code = payload.get("errorCode")
            suffix = f"（错误码 {code}）" if type(code) is int else ""
            raise BookkeepingError(f"ezBookkeeping 拒绝了请求{suffix}。")
        if not response.is_success:
            raise BookkeepingError(
                f"ezBookkeeping 请求失败（HTTP {response.status_code}）。"
            )
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or "result" not in payload
        ):
            if writing:
                raise WriteOutcomeUnknown(
                    f"invalid API response (HTTP {response.status_code})"
                )
            raise BookkeepingError("ezBookkeeping 返回了无法识别的响应。")
        return payload["result"]

    @staticmethod
    def offset(moment: datetime) -> int:
        return int(moment.utcoffset().total_seconds() // 60)

    def transaction_time(self, date_text: str, time_text: str = "") -> datetime:
        now = datetime.now(self.zone)
        date_text = date_text.strip()
        time_text = time_text.strip()
        try:
            if date_text and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date_text):
                raise ValueError
            day = date.fromisoformat(date_text) if date_text else now.date()
        except ValueError:
            raise BookkeepingError(
                "交易日期请填写 YYYY-MM-DD，或留空使用今天。"
            ) from None

        if not time_text and day == now.date():
            return now
        try:
            if time_text and not re.fullmatch(
                r"[0-9]{2}:[0-9]{2}(?::[0-9]{2})?", time_text
            ):
                raise ValueError
            clock = time.fromisoformat(time_text) if time_text else time(12)
        except ValueError:
            raise BookkeepingError(
                "交易时间请使用 24 小时制 HH:MM 或 HH:MM:SS。"
            ) from None

        moment = datetime.combine(day, clock, self.zone)
        try:
            if moment.timestamp() < 1:
                raise ValueError
            local = moment.astimezone(UTC).astimezone(self.zone)
        except (ValueError, OverflowError, OSError):
            raise BookkeepingError("交易日期或时间超出可用范围。") from None
        if local.replace(tzinfo=None) != moment.replace(tzinfo=None):
            raise BookkeepingError("该时间在记账时区不存在，请选择其他时间。")
        if moment.utcoffset() != moment.replace(fold=1).utcoffset():
            raise BookkeepingError("该时间在记账时区出现两次，请选择无歧义的时间。")
        return moment

    async def accounts(self) -> list[LedgerItem]:
        data = await self.request("GET", "accounts/list.json")
        if not isinstance(data, list):
            raise BookkeepingError("ezBookkeeping 返回了无法识别的账户列表。")
        result = []
        try:
            for parent in data:
                if parent.get("hidden"):
                    continue
                if parent.get("type") == 2 or parent.get("subAccounts"):
                    for child in parent.get("subAccounts") or []:
                        if not child.get("hidden"):
                            result.append(
                                LedgerItem(
                                    child["id"],
                                    f"{parent['name']} / {child['name']}",
                                    child["currency"],
                                )
                            )
                else:
                    result.append(
                        LedgerItem(parent["id"], parent["name"], parent["currency"])
                    )
        except (AttributeError, KeyError, TypeError):
            raise BookkeepingError("ezBookkeeping 返回了无法识别的账户列表。") from None
        return result

    async def categories(self, transaction_type: str) -> list[LedgerItem]:
        category_type = TRANSACTION_TYPES[transaction_type] - 1
        data = await self.request("GET", "transaction/categories/list.json")
        if not isinstance(data, dict):
            raise BookkeepingError("ezBookkeeping 返回了无法识别的分类列表。")
        try:
            return [
                LedgerItem(child["id"], f"{parent['name']} / {child['name']}")
                for parent in data.get(str(category_type)) or []
                if not parent.get("hidden")
                for child in parent.get("subCategories") or []
                if not child.get("hidden")
            ]
        except (AttributeError, KeyError, TypeError):
            raise BookkeepingError("ezBookkeeping 返回了无法识别的分类列表。") from None

    async def add(self, payload: dict) -> str:
        result = await self.request("POST", "transactions/add.json", json=payload)
        transaction_id = result.get("id") if isinstance(result, dict) else None
        if (
            not isinstance(transaction_id, str)
            or not transaction_id.isdecimal()
            or transaction_id == "0"
        ):
            raise WriteOutcomeUnknown("missing or invalid transaction ID")
        return transaction_id

    async def recent(self, count: int) -> list[dict]:
        result = await self.request(
            "GET", "transactions/list.json", params={"max_time": 0, "count": count}
        )
        if not isinstance(result, dict) or not isinstance(result.get("items"), list):
            raise BookkeepingError("ezBookkeeping 返回了无法识别的流水列表。")
        return result["items"][:count]
