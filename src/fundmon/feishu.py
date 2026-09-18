from __future__ import annotations

import json
from datetime import datetime

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody

from fundmon.estimate import FundEstimate
from fundmon.settings import Settings
from fundmon.trading import now

_client: lark.Client | None = None


def init_client(settings: Settings) -> lark.Client:
    global _client
    _client = (
        lark.Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret)
        .build()
    )
    return _client


def send_text(chat_id: str, text: str) -> None:
    if not text:
        return
    _send(chat_id, "text", json.dumps({"text": text}, ensure_ascii=False))


def send_estimate_card(chat_id: str, estimates: list[FundEstimate], at: datetime | None = None) -> None:
    at = at or now()
    overall = sum(item.pct_chg for item in estimates) / len(estimates) if estimates else 0.0
    template = "red" if overall > 0 else "green" if overall < 0 else "blue"
    title = f"基金盘中估值 {at.strftime('%H:%M')}"
    card = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": template,
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": _card_markdown(estimates)},
            }
        ],
    }
    _send(chat_id, "interactive", json.dumps(card, ensure_ascii=False))


def _card_markdown(estimates: list[FundEstimate]) -> str:
    blocks: list[str] = []
    for fund in estimates:
        lines = [
            f"**{fund.code} {fund.name}**  **{_fmt_pct(fund.pct_chg)}**",
            f"持仓截至 {fund.as_of or '未知'}",
        ]
        for item in fund.items:
            chg = "—" if item.pct_chg is None else _fmt_pct(item.pct_chg)
            lines.append(
                f"{item.holding.name}  {item.holding.weight:.2f}%  {chg}  贡献 {_fmt_pct(item.contribution)}"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) if blocks else "暂无估值数据"


def _fmt_pct(value: float) -> str:
    if value > 0:
        return f"+{value:.2f}%"
    return f"{value:.2f}%"


def _send(chat_id: str, msg_type: str, content: str) -> None:
    if _client is None:
        raise RuntimeError("飞书客户端尚未初始化")
    request = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        .build()
    )
    response = _client.im.v1.message.create(request)
    if not response.success():
        raise RuntimeError(f"飞书发消息失败 code={response.code} msg={response.msg}")
