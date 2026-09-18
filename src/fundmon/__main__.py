from __future__ import annotations

import json
import logging
import sys

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from fundmon.commands import handle_command
from fundmon.estimate import estimate_funds
from fundmon.feishu import init_client, send_estimate_card, send_text
from fundmon.scheduler import rebuild_jobs, start_scheduler
from fundmon.settings import load_settings
from fundmon.state import load_state

_LOG = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if len(sys.argv) > 1 and sys.argv[1] == "preview":
        _preview()
        return
    _run_bot()


def _run_bot() -> None:
    settings = load_settings()
    if not settings.feishu_app_id or not settings.feishu_app_secret:
        raise SystemExit("请在 .env 中配置 FEISHU_APP_ID 与 FEISHU_APP_SECRET")
    init_client(settings)
    start_scheduler()

    def on_message(event: P2ImMessageReceiveV1) -> None:
        _handle_message(event)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    _LOG.info("飞书长连接已启动，等待私聊指令")
    client.start()


def _handle_message(event: P2ImMessageReceiveV1) -> None:
    if event.event is None or event.event.message is None:
        return
    message = event.event.message
    if message.message_type != "text":
        return
    chat_id = message.chat_id or ""
    try:
        payload = json.loads(message.content or "{}")
    except json.JSONDecodeError:
        return
    text = str(payload.get("text", "")).strip()
    if not text:
        return
    result = handle_command(text, chat_id)
    try:
        if result.rebuild:
            rebuild_jobs()
        if result.send_estimate:
            _push_estimate(chat_id)
            if result.text:
                send_text(chat_id, result.text)
            return
        send_text(chat_id, result.text)
    except Exception:
        _LOG.exception("回复飞书失败")


def _push_estimate(chat_id: str) -> None:
    state = load_state()
    if not state.funds:
        send_text(chat_id, "请先添加基金，例如：添加 110022")
        return
    try:
        estimates = estimate_funds(state.funds)
        send_estimate_card(chat_id, estimates)
    except Exception as exc:
        _LOG.exception("估值失败")
        send_text(chat_id, f"估值失败：{exc}")


def _preview() -> None:
    state = load_state()
    codes = sys.argv[2:] or state.funds
    if not codes:
        raise SystemExit("请先添加基金，或执行：python -m fundmon preview 110022")
    estimates = estimate_funds(codes)
    for fund in estimates:
        print(f"{fund.code} {fund.name} {_fmt(fund.pct_chg)} 持仓截至 {fund.as_of}")
        for item in fund.items:
            chg = "—" if item.pct_chg is None else _fmt(item.pct_chg)
            print(
                f"  {item.holding.name} {item.holding.weight:.2f}% {chg} 贡献 {_fmt(item.contribution)}"
            )


def _fmt(value: float) -> str:
    if value > 0:
        return f"+{value:.2f}%"
    return f"{value:.2f}%"


if __name__ == "__main__":
    main()
