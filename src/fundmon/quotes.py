from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

_LOG = logging.getLogger(__name__)
_TENCENT_URL = "https://qt.gtimg.cn/q={symbols}"
_EASTMONEY_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.qq.com/",
}


@dataclass(frozen=True)
class Quote:
    code: str
    name: str
    price: float | None
    pct_chg: float | None


def fetch_quotes(stocks: list[tuple[int, str]]) -> dict[str, Quote]:
    unique = list(dict.fromkeys(stocks))
    if not unique:
        return {}
    quotes: dict[str, Quote] = {}
    try:
        quotes = _fetch_tencent(unique)
    except Exception:
        _LOG.warning("腾讯行情失败，改用东财", exc_info=True)
    missing = [
        item for item in unique if quotes.get(item[1]) is None or quotes[item[1]].pct_chg is None
    ]
    if missing:
        try:
            quotes.update(_fetch_eastmoney(missing))
        except Exception:
            _LOG.warning("东财行情失败", exc_info=True)
    return quotes


def _fetch_tencent(stocks: list[tuple[int, str]]) -> dict[str, Quote]:
    symbols = ",".join(_tencent_symbol(market, code) for market, code in stocks)
    url = _TENCENT_URL.format(symbols=symbols)
    with httpx.Client(headers=_HEADERS, timeout=20.0, trust_env=False) as client:
        response = client.get(url)
        response.raise_for_status()
        text = _decode(response.content)
    quotes: dict[str, Quote] = {}
    for line in text.strip().split(";"):
        line = line.strip()
        if not line or "=" not in line:
            continue
        _left, right = line.split("=", 1)
        fields = right.strip().strip('"').split("~")
        if len(fields) < 33:
            continue
        code = fields[2]
        quotes[code] = Quote(
            code=code,
            name=fields[1],
            price=_to_float(fields[3]),
            pct_chg=_to_float(fields[32]),
        )
    return quotes


def _fetch_eastmoney(stocks: list[tuple[int, str]]) -> dict[str, Quote]:
    params = {
        "fltt": "2",
        "np": "1",
        "secids": ",".join(f"{market}.{code}" for market, code in stocks),
        "fields": "f12,f13,f14,f2,f3",
    }
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    with httpx.Client(headers=headers, timeout=20.0, trust_env=False) as client:
        response = client.get(_EASTMONEY_URL, params=params)
        response.raise_for_status()
        payload = response.json()
    rows = ((payload.get("data") or {}).get("diff")) or []
    quotes: dict[str, Quote] = {}
    for row in rows:
        code = str(row.get("f12", ""))
        quotes[code] = Quote(
            code=code,
            name=str(row.get("f14", "")),
            price=_to_float(row.get("f2")),
            pct_chg=_to_float(row.get("f3")),
        )
    return quotes


def _tencent_symbol(market: int, code: str) -> str:
    if code.startswith(("8", "4")):
        return f"bj{code}"
    if market == 1:
        return f"sh{code}"
    return f"sz{code}"


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _to_float(value: object) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
