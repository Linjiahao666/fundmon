from datetime import date, datetime
from zoneinfo import ZoneInfo
import logging

import chinese_calendar

_LOG = logging.getLogger(__name__)
SHANGHAI = ZoneInfo("Asia/Shanghai")


def now() -> datetime:
    return datetime.now(SHANGHAI)


def is_trading_day(day: date | None = None) -> bool:
    day = day or now().date()
    if day.weekday() >= 5:
        return False
    try:
        return not chinese_calendar.is_holiday(day)
    except NotImplementedError:
        _LOG.error("节假日数据未覆盖 %s，按休市处理", day.isoformat())
        return False


def is_trading_session(at: datetime | None = None) -> bool:
    at = at or now()
    if not is_trading_day(at.date()):
        return False
    clock = (at.hour, at.minute)
    morning = (9, 30) <= clock <= (11, 30)
    afternoon = (13, 0) <= clock <= (15, 0)
    return morning or afternoon
