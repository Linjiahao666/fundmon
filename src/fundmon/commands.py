from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from fundmon.holdings import ensure_holdings
from fundmon.state import AppState, load_state, normalize_clock, update_state
from fundmon.trading import is_trading_session

_LOG = logging.getLogger(__name__)
_HELP = """可用指令：
开启 / 关闭
分钟 15
小时 1
定点 10:00 14:50
取消分钟 / 取消小时 / 取消定点 10:00 / 取消全部
提醒
添加 110022
删除 110022
列表
估值"""


@dataclass(frozen=True)
class CommandResult:
    text: str
    send_estimate: bool = False
    rebuild: bool = False


def handle_command(text: str, chat_id: str) -> CommandResult:
    text = text.strip()
    if text == "开启":
        return _enable(chat_id)
    if text == "关闭":
        return _disable(chat_id)
    if text == "提醒":
        return CommandResult(text=_schedule_text(load_state()))
    if text == "列表":
        return CommandResult(text=_funds_text(load_state()))
    if text == "估值":
        return _estimate(chat_id)
    if text == "帮助":
        return CommandResult(text=_HELP)
    if text == "取消全部":
        return _clear_schedules(chat_id)
    if text == "取消分钟":
        return _set_minute(chat_id, None)
    if text == "取消小时":
        return _set_hour(chat_id, None)
    if text.startswith("取消定点"):
        return _remove_times(chat_id, text)
    minute = re.fullmatch(r"分钟\s*(\d+)", text)
    if minute:
        value = int(minute.group(1))
        if value < 1:
            return CommandResult(text="格式：分钟 15")
        return _set_minute(chat_id, value)
    hour = re.fullmatch(r"小时\s*(\d+)", text)
    if hour:
        value = int(hour.group(1))
        if value < 1:
            return CommandResult(text="格式：小时 1")
        return _set_hour(chat_id, value)
    if text.startswith("定点"):
        return _add_times(chat_id, text)
    if text.startswith("添加"):
        return _add_funds(chat_id, text)
    if text.startswith("删除"):
        return _remove_funds(chat_id, text)
    return CommandResult(text=_HELP)


def _enable(chat_id: str) -> CommandResult:
    state = update_state(lambda current: _bind(current, chat_id, enabled=True))
    extra = _schedule_text(state)
    if not state.funds:
        return CommandResult(text=f"已开启通知。请先添加基金，例如：添加 110022\n{extra}", rebuild=True)
    if is_trading_session():
        return CommandResult(text=f"已开启通知。\n{extra}", send_estimate=True, rebuild=True)
    return CommandResult(text=f"已开启通知，开盘后按规则推送。\n{extra}", rebuild=True)


def _disable(chat_id: str) -> CommandResult:
    update_state(lambda current: _bind(current, chat_id, enabled=False))
    return CommandResult(text="已关闭通知，停止拉取行情。", rebuild=True)


def _estimate(chat_id: str) -> CommandResult:
    state = load_state()
    if not state.funds:
        return CommandResult(text="请先添加基金，例如：添加 110022")
    update_state(lambda current: _bind(current, chat_id))
    return CommandResult(text="", send_estimate=True)


def _set_minute(chat_id: str, minutes: int | None) -> CommandResult:
    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.minute_interval = minutes

    state = update_state(mutate)
    if minutes is None:
        return CommandResult(text=f"已取消分钟提醒。\n{_schedule_text(state)}", rebuild=True)
    return CommandResult(text=f"已设置每 {minutes} 分钟提醒。\n{_schedule_text(state)}", rebuild=True)


def _set_hour(chat_id: str, hours: int | None) -> CommandResult:
    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.hour_interval = hours

    state = update_state(mutate)
    if hours is None:
        return CommandResult(text=f"已取消小时提醒。\n{_schedule_text(state)}", rebuild=True)
    return CommandResult(text=f"已设置每 {hours} 小时提醒。\n{_schedule_text(state)}", rebuild=True)


def _add_times(chat_id: str, text: str) -> CommandResult:
    times = _parse_times(text.removeprefix("定点"))
    if not times:
        return CommandResult(text="格式：定点 10:00 14:50")

    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.fixed_times = sorted(set(current.fixed_times) | set(times))

    state = update_state(mutate)
    return CommandResult(text=f"已添加定点 {', '.join(times)}。\n{_schedule_text(state)}", rebuild=True)


def _remove_times(chat_id: str, text: str) -> CommandResult:
    times = _parse_times(text.removeprefix("取消定点"))
    if not times:
        return CommandResult(text="格式：取消定点 10:00")

    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.fixed_times = [item for item in current.fixed_times if item not in times]

    state = update_state(mutate)
    return CommandResult(text=f"已取消定点 {', '.join(times)}。\n{_schedule_text(state)}", rebuild=True)


def _clear_schedules(chat_id: str) -> CommandResult:
    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.minute_interval = None
        current.hour_interval = None
        current.fixed_times = []

    update_state(mutate)
    return CommandResult(text="已取消全部提醒规则。", rebuild=True)


def _add_funds(chat_id: str, text: str) -> CommandResult:
    codes = _parse_codes(text.removeprefix("添加"))
    if not codes:
        return CommandResult(text="格式：添加 110022")
    try:
        funds = ensure_holdings(codes, force=True)
    except (httpx.HTTPError, ValueError) as exc:
        _LOG.exception("添加基金失败")
        return CommandResult(text=f"添加失败：{exc}")

    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        for code in codes:
            if code not in current.funds:
                current.funds.append(code)

    state = update_state(mutate)
    names = "、".join(f"{code} {funds[code].name}" for code in codes)
    return CommandResult(text=f"已添加 {names}。\n{_funds_text(state)}")


def _remove_funds(chat_id: str, text: str) -> CommandResult:
    codes = _parse_codes(text.removeprefix("删除"))
    if not codes:
        return CommandResult(text="格式：删除 110022")

    def mutate(current: AppState) -> None:
        _bind(current, chat_id)
        current.funds = [item for item in current.funds if item not in codes]

    state = update_state(mutate)
    return CommandResult(text=f"已删除 {', '.join(codes)}。\n{_funds_text(state)}")


def _bind(state: AppState, chat_id: str, enabled: bool | None = None) -> None:
    if chat_id:
        state.chat_id = chat_id
    if enabled is not None:
        state.enabled = enabled


def _parse_codes(text: str) -> list[str]:
    return [item for item in text.replace("，", " ").split() if item.isdigit() and len(item) == 6]


def _parse_times(text: str) -> list[str]:
    times: list[str] = []
    for item in text.replace("，", " ").replace(",", " ").split():
        try:
            times.append(normalize_clock(item))
        except ValueError:
            continue
    return times


def _schedule_text(state: AppState) -> str:
    lines = [f"通知：{'开启' if state.enabled else '关闭'}"]
    if state.minute_interval:
        lines.append(f"分钟间隔：{state.minute_interval}")
    if state.hour_interval:
        lines.append(f"小时间隔：{state.hour_interval}")
    if state.fixed_times:
        lines.append(f"固定时刻：{', '.join(state.fixed_times)}")
    if not state.minute_interval and not state.hour_interval and not state.fixed_times:
        lines.append("尚未设置提醒规则")
    return "\n".join(lines)


def _funds_text(state: AppState) -> str:
    if not state.funds:
        return "自选基金为空"
    return "自选基金：" + "、".join(state.funds)
