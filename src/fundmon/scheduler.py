from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from fundmon.estimate import estimate_funds
from fundmon.feishu import send_estimate_card, send_text
from fundmon.state import load_state
from fundmon.trading import SHANGHAI, is_trading_session

_LOG = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone=SHANGHAI)


def start_scheduler() -> None:
    if not _scheduler.running:
        _scheduler.start()
    rebuild_jobs()


def rebuild_jobs() -> None:
    _scheduler.remove_all_jobs()
    state = load_state()
    if not state.enabled:
        _LOG.info("通知已关闭，不注册定时任务")
        return
    if state.minute_interval:
        trigger = (
            CronTrigger(minute=f"*/{state.minute_interval}", timezone=SHANGHAI)
            if state.minute_interval <= 59
            else IntervalTrigger(minutes=state.minute_interval, timezone=SHANGHAI)
        )
        _scheduler.add_job(push_if_open, trigger, id="minute", replace_existing=True)
    if state.hour_interval:
        trigger = (
            CronTrigger(hour=f"*/{state.hour_interval}", minute=0, timezone=SHANGHAI)
            if state.hour_interval <= 23
            else IntervalTrigger(hours=state.hour_interval, timezone=SHANGHAI)
        )
        _scheduler.add_job(push_if_open, trigger, id="hour", replace_existing=True)
    for clock in state.fixed_times:
        hour_text, minute_text = clock.split(":")
        _scheduler.add_job(
            push_if_open,
            CronTrigger(hour=int(hour_text), minute=int(minute_text), timezone=SHANGHAI),
            id=f"at-{clock}",
            replace_existing=True,
        )
    _LOG.info(
        "已重建定时任务 minute=%s hour=%s times=%s",
        state.minute_interval,
        state.hour_interval,
        state.fixed_times,
    )


def push_if_open() -> None:
    state = load_state()
    if not state.enabled or not state.chat_id or not state.funds:
        return
    if not is_trading_session():
        _LOG.debug("非交易时段，跳过推送")
        return
    try:
        estimates = estimate_funds(state.funds)
        send_estimate_card(state.chat_id, estimates)
    except Exception as exc:
        _LOG.exception("定时估值推送失败")
        try:
            send_text(state.chat_id, f"定时估值失败：{exc}")
        except Exception:
            _LOG.exception("失败通知发送失败")
