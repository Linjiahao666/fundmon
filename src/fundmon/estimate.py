from __future__ import annotations

from dataclasses import dataclass

from fundmon.holdings import FundHoldings, Holding, ensure_holdings
from fundmon.quotes import Quote, fetch_quotes


@dataclass(frozen=True)
class HoldingEstimate:
    holding: Holding
    pct_chg: float | None
    contribution: float


@dataclass(frozen=True)
class FundEstimate:
    code: str
    name: str
    as_of: str
    period: str
    pct_chg: float
    items: list[HoldingEstimate]


def estimate_funds(codes: list[str], force_holdings: bool = False) -> list[FundEstimate]:
    funds = ensure_holdings(codes, force=force_holdings)
    quotes = fetch_quotes(
        [(item.market, item.code) for fund in funds.values() for item in fund.holdings]
    )
    if not quotes:
        raise ValueError("没有拉到任何股票行情")
    return [estimate_fund(funds[code], quotes) for code in codes if code in funds]


def estimate_fund(fund: FundHoldings, quotes: dict[str, Quote]) -> FundEstimate:
    items: list[HoldingEstimate] = []
    total = 0.0
    priced = 0
    for holding in fund.holdings:
        quote = quotes.get(holding.code)
        pct_chg = quote.pct_chg if quote is not None else None
        if pct_chg is None:
            items.append(HoldingEstimate(holding=holding, pct_chg=None, contribution=0.0))
            continue
        contribution = holding.weight / 100.0 * pct_chg
        total += contribution
        priced += 1
        items.append(HoldingEstimate(holding=holding, pct_chg=pct_chg, contribution=contribution))
    if priced == 0:
        raise ValueError(f"{fund.code} 没有可用的股票行情")
    return FundEstimate(
        code=fund.code,
        name=fund.name,
        as_of=fund.as_of,
        period=fund.period,
        pct_chg=total,
        items=items,
    )
