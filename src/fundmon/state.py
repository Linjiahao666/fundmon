from __future__ import annotations

import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from fundmon.settings import DATA_DIR

_LOCK = threading.Lock()
_STATE_PATH = DATA_DIR / "state.json"


@dataclass
class AppState:
    enabled: bool = False
    chat_id: str = ""
    funds: list[str] = field(default_factory=list)
    minute_interval: int | None = None
    hour_interval: int | None = None
    fixed_times: list[str] = field(default_factory=list)


def _default_state() -> AppState:
    return AppState()


def load_state() -> AppState:
    with _LOCK:
        return _read_unlocked()


def save_state(state: AppState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        _write_unlocked(state)


def update_state(mutator: Callable[[AppState], None]) -> AppState:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        state = _read_unlocked()
        mutator(state)
        _write_unlocked(state)
        return state


def _read_unlocked() -> AppState:
    if not _STATE_PATH.exists():
        return _default_state()
    raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    return AppState(
        enabled=bool(raw.get("enabled", False)),
        chat_id=str(raw.get("chat_id", "")),
        funds=[str(code) for code in raw.get("funds", [])],
        minute_interval=_optional_int(raw.get("minute_interval")),
        hour_interval=_optional_int(raw.get("hour_interval")),
        fixed_times=_read_times(raw.get("fixed_times", [])),
    )


def _write_unlocked(state: AppState) -> None:
    payload = asdict(state)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    tmp = _STATE_PATH.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(_STATE_PATH)


def _read_times(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    times: list[str] = []
    for item in values:
        try:
            times.append(normalize_clock(item))
        except ValueError:
            continue
    return sorted(set(times))


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def normalize_clock(value: object) -> str:
    text = str(value).strip()
    hour_text, minute_text = text.split(":")
    hour = int(hour_text)
    minute = int(minute_text)
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("时刻超出范围")
    return f"{hour:02d}:{minute:02d}"
