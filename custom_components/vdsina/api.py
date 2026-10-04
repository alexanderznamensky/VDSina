"""Read-only async VDSina API client and normalization."""
from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

import aiohttp

from .const import API_HOSTS

MOSCOW = ZoneInfo("Europe/Moscow")


class VDSinaError(Exception):
    """API/network/payload error, with no response bodies or secrets."""


class InvalidAuth(VDSinaError):
    """Token rejected."""


class NotFound(VDSinaError):
    """Service disappeared during a refresh."""


def number(value: Any) -> float | None:
    """Keep missing/invalid amounts unknown, including non-finite numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        converted = float(result) if result.is_finite() else None
        return converted if converted is not None and math.isfinite(converted) else None
    except (InvalidOperation, TypeError, ValueError, OverflowError):
        return None


def parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def traffic_gb(bandwidth: Any, period: str = "current_month") -> float | None:
    """API bandwidth is in bytes; expose decimal GB, never invent missing usage."""
    if not isinstance(bandwidth, dict):
        return None
    value = number(bandwidth.get(period))
    return value / 1_000_000_000 if value is not None and value >= 0 else None


def traffic_gib(bandwidth: Any, period: str = "current_month") -> float | None:
    value = traffic_gb(bandwidth, period)
    return value * 1_000_000_000 / (1024 ** 3) if value is not None else None


def monthly_directions(rows: Any, start: datetime, end: datetime) -> dict[str, Any]:
    """Sum external-network hourly buckets only within the Moscow calendar month."""
    if rows is None or rows == []:
        return {"incoming_bytes": None, "outgoing_bytes": None, "samples": 0}
    if not isinstance(rows, list):
        raise VDSinaError("Invalid server statistics response")
    incoming = outgoing = 0
    incoming_valid = outgoing_valid = True
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or (stamp := parse_timestamp(row.get("dt"))) is None:
            raise VDSinaError("Invalid hourly statistics timestamp")
        if not start <= stamp <= end:
            continue
        if stamp in seen:
            raise VDSinaError("Duplicate hourly statistics timestamp")
        seen.add(stamp)
        stat = row.get("stat")
        if not isinstance(stat, dict):
            raise VDSinaError("Invalid hourly statistics values")
        rx, tx = number(stat.get("vnet_rx")), number(stat.get("vnet_tx"))
        if rx is None or rx < 0:
            incoming_valid = False
        else:
            incoming += rx
        if tx is None or tx < 0:
            outgoing_valid = False
        else:
            outgoing += tx
    return {"incoming_bytes": incoming if seen and incoming_valid else None,
            "outgoing_bytes": outgoing if seen and outgoing_valid else None,
            "samples": len(seen)}


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    return result.replace(tzinfo=MOSCOW) if result.tzinfo is None else result


def days_left(value: Any) -> int | None:
    due = parse_date(value)
    return (due - datetime.now(MOSCOW).date()).days if due else None


def payment_message(name: str, forecast: Any) -> str:
    remaining = days_left(forecast)
    if remaining is None:
        return f"VDSina — {name}: прогноз отключения не определён"
    if remaining < 0:
        return f"VDSina — {name}: прогнозируемая дата отключения уже прошла!"
    if remaining == 0:
        return f"VDSina — {name}: прогноз отключения аккаунта сегодня!"
    return f"VDSina — {name}: до прогнозируемого отключения аккаунта {remaining} дн."


def ip_addresses(value: Any) -> list[str]:
    """Accept the current object and legacy arrays, preserving all addresses."""
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item["ip"]) for item in value
                              if isinstance(item, dict) and item.get("ip")))


def normalize_server(item: dict[str, Any], forecast: Any) -> dict[str, Any]:
    """Use an allowlist: never expose API credentials through attributes."""
    resource = item.get("data") or {}
    if not isinstance(resource, dict):
        resource = {}
    def named(key: str) -> Any:
        obj = item.get(key)
        return obj.get("name") if isinstance(obj, dict) else None
    def capacity(key: str) -> Any:
        obj = resource.get(key)
        return obj.get("value") if isinstance(obj, dict) else None
    ips = ip_addresses(item.get("ip"))
    dc = item.get("datacenter") or {}
    traff = resource.get("traff") or {}
    limit_bytes = number(traff.get("bytes")) if isinstance(traff, dict) else None
    return {
        "id": str(item["id"]), "name": item.get("name") or f"VDS #{item['id']}",
        "status": item.get("status") or "unknown", "status_text": item.get("status_text"),
        "ip": ips[0] if ips else None, "ips": ips, "hostname": item.get("host"),
        "tariff": named("server-plan"), "datacenter": named("datacenter"),
        "country": dc.get("country") if isinstance(dc, dict) else None,
        "os": named("template"), "cpu": capacity("cpu"), "ram": capacity("ram"),
        "disk": capacity("disk"), "traffic_limit": capacity("traff"),
        "traffic_limit_bytes": limit_bytes if limit_bytes is not None and limit_bytes > 0 else None,
        "service_end": item.get("end"), "created": item.get("created"),
        "autoprolong": item.get("autoprolong"), "autorun": item.get("autorun"),
        "bandwidth": item.get("bandwidth") if isinstance(item.get("bandwidth"), dict) else None,
        "forecast": forecast,
    }


class VDSinaClient:
    """Use only the selected official host; never forward auth on redirects."""

    def __init__(self, session: aiohttp.ClientSession, token: str, host: str) -> None:
        if host not in API_HOSTS:
            raise ValueError("Unsupported API host")
        token = token.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or "\n" in token or "\r" in token:
            raise InvalidAuth("Empty or invalid token")
        self.session = session
        self._token = token
        self.host = host
        self._raw_auth = False

    async def _request(self, path: str, *, retry_auth: bool = True, params: dict | None = None) -> Any:
        authorization = self._token if self._raw_auth else f"Bearer {self._token}"
        try:
            async with self.session.get(
                f"https://{self.host}/v1/{path}",
                headers={"Authorization": authorization, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30), allow_redirects=False,
                params=params,
            ) as response:
                status = response.status
                if status == 401:
                    payload = None
                elif status == 404:
                    raise NotFound("API object not found")
                elif status == 403:
                    raise VDSinaError("API access denied: check user permissions (403)")
                elif status != 200:
                    raise VDSinaError(f"API returned HTTP {status}")
                else:
                    try:
                        payload = await response.json()
                    except (ValueError, aiohttp.ContentTypeError) as err:
                        raise VDSinaError("API returned invalid JSON") from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise VDSinaError("API connection or timeout error") from err
        if status == 401:
            # Current OpenAPI uses Bearer; older API versions use the bare token.
            # Retry once, on the same chosen host, without following redirects.
            if retry_auth and not self._raw_auth:
                self._raw_auth = True
                return await self._request(path, retry_auth=False, params=params)
            raise InvalidAuth("API token rejected (401)")
        if not isinstance(payload, dict):
            raise VDSinaError("Invalid API response structure")
        if payload.get("status") != "ok":
            code = str(payload.get("status_code", ""))
            if code == "401":
                raise InvalidAuth("API token rejected")
            if code == "404":
                raise NotFound("API object not found")
            raise VDSinaError("API reported an error; check permissions and service availability")
        return payload.get("data")

    async def account(self) -> dict[str, Any]:
        data = await self._request("account")
        if not isinstance(data, dict) or not isinstance(data.get("account"), dict) or not data["account"].get("id"):
            raise VDSinaError("Account ID missing in API response")
        return data

    async def snapshot(self) -> dict[str, Any]:
        # Authenticate before dispatching concurrent requests/fallbacks.
        async with asyncio.timeout(120):
            account = await self.account()
            balance = await self._request("account.balance")
            listing = await self._request("server")
            if balance is None:
                balance = {}
            if not isinstance(balance, dict):
                raise VDSinaError("Invalid account balance response")
            if listing is None:
                listing = []
            if not isinstance(listing, list) or any(not isinstance(s, dict) or not s.get("id") for s in listing):
                raise VDSinaError("Invalid server list response")
            if any(not str(s["id"]).isdigit() for s in listing):
                raise VDSinaError("Invalid server ID in response")
            semaphore = asyncio.Semaphore(4)
            period_end = datetime.now(MOSCOW)
            period_start = period_end.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            async def detail(item: dict[str, Any]) -> dict[str, Any] | None:
                if item.get("status") == "deleted":
                    return None
                async with semaphore:
                    try:
                        data = await self._request(f"server/{int(item['id'])}")
                    except NotFound:
                        # Server was removed after the server-list request.
                        return None
                if not isinstance(data, dict) or str(data.get("id")) != str(item["id"]):
                    raise VDSinaError("Invalid server detail response")
                server = normalize_server({**item, **data}, account.get("forecast"))
                async with semaphore:
                    try:
                        rows = await self._request(f"server.stat/{int(item['id'])}", params={
                            "from": period_start.strftime("%Y-%m-%d %H:%M:%S"),
                            "to": period_end.strftime("%Y-%m-%d %H:%M:%S"),
                        })
                        server["traffic_statistics"] = monthly_directions(rows, period_start, period_end)
                        server["traffic_statistics_error"] = None
                    except InvalidAuth:
                        raise
                    except VDSinaError as err:
                        # Statistics permissions/errors must not hide balances and VPS status.
                        server["traffic_statistics"] = {"incoming_bytes": None, "outgoing_bytes": None, "samples": 0}
                        server["traffic_statistics_error"] = str(err)
                server["traffic_statistics_period"] = period_start.strftime("%Y-%m")
                return server
            results = await asyncio.gather(*(detail(s) for s in listing), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    raise result
            servers = {s["id"]: s for s in results if isinstance(s, dict)}
            return {
                "account_id": str(account["account"]["id"]),
                "account_name": account["account"].get("name"),
                "forecast": account.get("forecast"),
                "balance": number(balance.get("real")),
                "bonus": number(balance.get("bonus")),
                "partner": number(balance.get("partner")),
                "currency": balance.get("currency") or ("USD" if self.host.endswith(".com") else "RUB"),
                "servers": servers,
            }
