from __future__ import annotations

import json
import logging
import re
import threading
from dataclasses import asdict, dataclass

import httpx

from fundmon.settings import DATA_DIR
from fundmon.trading import now

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_HOLDINGS_PATH = DATA_DIR / "holdings.json"
_F10_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://fundf10.eastmoney.com/",
}
_ROW_RE = re.compile(
    r"unify/r/(?P<market>\d+)\.(?P<code>\d{6})'>(?P=code)</a></td>"
    r"<td class='tol'><a[^>]*>(?P<name>[^<]+)</a></td>"
    r".*?class='xglj'>.*?</td>"
    r"<td class='tor'>(?P<weight>[\d.]+)%</td>",
    re.DOTALL,
)


@dataclass(frozen=True)
class Holding:
    code: str
    market: int
    name: str
    weight: float


@dataclass(frozen=True)
class FundHoldings:
    code: str
    name: str
    as_of: str
    period: str
    holdings: list[Holding]


def ensure_holdings(codes: list[str], force: bool = False) -> dict[str, FundHoldings]:
    today = now().date().isoformat()
    result: dict[str, FundHoldings] = {}
    for code in codes:
        cached = _cached_fund(code)
        if not force and _is_fresh(cached, today):
            result[code] = _fund_from_dict(code, cached)
            continue
        try:
            fund = fetch_holdings(code)
        except (httpx.HTTPError, ValueError):
            if cached is not None:
                _LOG.exception("刷新 %s 持仓失败，沿用缓存", code)
                result[code] = _fund_from_dict(code, cached)
                continue
            raise
        _store_fund(code, fund, today)
        result[code] = fund
    return result


def fetch_holdings(code: str) -> FundHoldings:
    params = {"type": "jjcc", "code": code, "topline": "10", "year": "", "month": ""}
    with httpx.Client(headers=_HEADERS, timeout=20.0, trust_env=False) as client:
        response = client.get(_F10_URL, params=params)
        response.raise_for_status()
        text = response.text
    html = _extract_content(text)
    if not html:
        raise ValueError(f"未找到基金 {code} 的持仓数据")
    table = _first_table(html)
    name = _search(r"title='([^']+)'", html, f"基金{code}")
    period = _search(r"(\d{4}年\d季度)股票投资明细", html, "")
    as_of = _search(r"截止至：<font class='px12'>(\d{4}-\d{2}-\d{2})</font>", html, "")
    holdings = [
        Holding(
            code=match.group("code"),
            market=int(match.group("market")),
            name=match.group("name"),
            weight=float(match.group("weight")),
        )
        for match in _ROW_RE.finditer(table)
        if match.group("market") in {"0", "1", "90"}
    ][:10]
    if not holdings:
        raise ValueError(f"基金 {code} 没有可估算的 A 股前十大持仓")
    _LOG.info("已更新 %s %s 持仓，截至 %s", code, name, as_of)
    return FundHoldings(code=code, name=name, as_of=as_of, period=period, holdings=holdings)


def _extract_content(text: str) -> str:
    start_token = 'content:"'
    start = text.find(start_token)
    if start < 0:
        return ""
    start += len(start_token)
    end = text.find('",arryear', start)
    if end < 0:
        end = text.find('", arryear', start)
    if end < 0:
        return ""
    return text[start:end]


def _first_table(html: str) -> str:
    match = re.search(r"<table[\s\S]*?</table>", html)
    return match.group(0) if match else html


def _search(pattern: str, text: str, fallback: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else fallback


def _cached_fund(code: str) -> dict[str, object] | None:
    with _LOCK:
        cache = _load_cache()
        funds_cache = cache.get("funds")
        if not isinstance(funds_cache, dict):
            return None
        cached = funds_cache.get(code)
        return cached if isinstance(cached, dict) else None


def _is_fresh(cached: dict[str, object] | None, today: str) -> bool:
    return cached is not None and cached.get("fetched_on") == today


def _store_fund(code: str, fund: FundHoldings, today: str) -> None:
    with _LOCK:
        cache = _load_cache()
        funds_cache = cache.get("funds")
        if not isinstance(funds_cache, dict):
            funds_cache = {}
            cache["funds"] = funds_cache
        funds_cache[code] = _fund_to_dict(fund, today)
        _save_cache(cache)


def _load_cache() -> dict[str, object]:
    if not _HOLDINGS_PATH.exists():
        return {"funds": {}}
    return json.loads(_HOLDINGS_PATH.read_text(encoding="utf-8"))


def _save_cache(cache: dict[str, object]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _HOLDINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_HOLDINGS_PATH)


def _fund_to_dict(fund: FundHoldings, fetched_on: str) -> dict[str, object]:
    return {
        "fetched_on": fetched_on,
        "name": fund.name,
        "as_of": fund.as_of,
        "period": fund.period,
        "holdings": [asdict(item) for item in fund.holdings],
    }


def _fund_from_dict(code: str, raw: dict[str, object]) -> FundHoldings:
    holdings_raw = raw.get("holdings", [])
    holdings: list[Holding] = []
    if isinstance(holdings_raw, list):
        for item in holdings_raw:
            if not isinstance(item, dict):
                continue
            holdings.append(
                Holding(
                    code=str(item["code"]),
                    market=int(item["market"]),
                    name=str(item["name"]),
                    weight=float(item["weight"]),
                )
            )
    return FundHoldings(
        code=code,
        name=str(raw.get("name", f"基金{code}")),
        as_of=str(raw.get("as_of", "")),
        period=str(raw.get("period", "")),
        holdings=holdings,
    )
