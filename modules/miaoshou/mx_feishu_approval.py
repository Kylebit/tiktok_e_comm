"""飞书 MX 上架审批卡片（与对话框确认卡同结构：主图 + 价格表 + 审批按钮）。"""

from __future__ import annotations

import os
from typing import Any

from modules.miaoshou.mx_confirm import MxConfirmCard, MxGroupConfirmCard, format_confirm_card_dialog, format_group_confirm_card_dialog


def _strip_dialog_actions(md: str) -> str:
    lines = []
    for line in md.splitlines():
        if line.startswith("确认上架请回复") or line.startswith("取消请回复"):
            continue
        if line.startswith("确认整组上架请回复") or line.startswith("取消请回复：**取消"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def confirm_card_markdown(card: MxConfirmCard) -> str:
    """与对话框确认卡相同的 Markdown（不含「确认 xxx」回复提示）。"""
    return _strip_dialog_actions(format_confirm_card_dialog(card))


def group_confirm_card_markdown(card: MxGroupConfirmCard) -> str:
    return _strip_dialog_actions(format_group_confirm_card_dialog(card))


def _approval_form_block(
    *,
    task_id: str,
    title: str,
    confirm_token: str | None = None,
    match_key: str | None = None,
) -> dict[str, Any]:
    """表单：修改意见输入框 + 审批按钮（form_submit 回传 modify_note）。"""
    base: dict[str, Any] = {"task_id": task_id, "title": title}
    if confirm_token:
        base["confirm_token"] = confirm_token
    if match_key:
        base["match_key"] = match_key

    mk_hint = match_key or "0810"
    return {
        "tag": "form",
        "name": f"mx_approval_{match_key or 'sku'}",
        "elements": [
            {
                "tag": "markdown",
                "content": (
                    "**修改意见**（可选）\n"
                    "如需改尺寸/重量/价格等，请填写后点 **提交修改意见**；"
                    "确认无误再点 **批准发布**。"
                ),
            },
            {
                "tag": "input",
                "name": "modify_note",
                "input_type": "multiline_text",
                "rows": 3,
                "auto_resize": True,
                "max_length": 500,
                "placeholder": {
                    "tag": "plain_text",
                    "content": f"例如：{mk_hint} 尺寸改成 15×15×6，重量 200g",
                },
            },
            {
                "tag": "button",
                "name": "btn_revision",
                "action_type": "form_submit",
                "text": {"tag": "plain_text", "content": "提交修改意见"},
                "type": "default",
                "value": {**base, "action": "request_revision"},
            },
            {
                "tag": "button",
                "name": "btn_approve",
                "action_type": "form_submit",
                "text": {"tag": "plain_text", "content": "批准发布"},
                "type": "primary",
                "value": {**base, "action": "approve"},
            },
            {
                "tag": "button",
                "name": "btn_reject",
                "action_type": "form_submit",
                "text": {"tag": "plain_text", "content": "拒绝"},
                "type": "danger",
                "value": {**base, "action": "reject"},
            },
        ],
    }


def _approval_buttons(
    *,
    task_id: str,
    title: str,
    confirm_token: str | None = None,
    match_key: str | None = None,
) -> dict[str, Any]:
    """兼容旧调用：转为带输入框的表单块。"""
    return _approval_form_block(
        task_id=task_id,
        title=title,
        confirm_token=confirm_token,
        match_key=match_key,
    )


def _image_elements(image_url: str) -> list[dict[str, Any]]:
    if not image_url:
        return []
    elements: list[dict[str, Any]] = []
    try:
        from modules.hub.feishu_app import upload_image_from_url

        img_key = upload_image_from_url(image_url)
        if img_key:
            elements.append(
                {
                    "tag": "img",
                    "img_key": img_key,
                    "alt": {"tag": "plain_text", "content": "主图"},
                    "mode": "fit_horizontal",
                    "preview": True,
                }
            )
            return elements
    except Exception:
        pass
    elements.append(
        {
            "tag": "markdown",
            "content": f"[查看主图]({image_url})",
        }
    )
    return elements


def build_single_mx_approval_card(
    card: MxConfirmCard,
    *,
    task_id: str,
    title: str | None = None,
    risk_note: str = "",
) -> dict[str, Any]:
    """飞书 interactive 卡片 JSON（单 SKU）。"""
    header_title = title or f"MX 上架审批 · {card.match_key}"
    md = confirm_card_markdown(card)
    if risk_note:
        md = f"**审批说明** · {risk_note}\n\n---\n\n{md}"

    elements: list[dict[str, Any]] = []
    elements.extend(_image_elements(card.main_image_url))
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": md[:8000]}})
    elements.append({"tag": "hr"})
    elements.append(
        _approval_form_block(
            task_id=task_id,
            title=header_title,
            confirm_token=card.token,
            match_key=card.match_key,
        )
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": header_title[:100]},
        },
        "elements": elements,
    }


def build_group_mx_approval_card(
    card: MxGroupConfirmCard,
    *,
    task_id: str,
    title: str | None = None,
    risk_note: str = "",
) -> dict[str, Any]:
    keys_label = f"{card.match_keys[0]}–{card.match_keys[-1]}" if len(card.match_keys) > 1 else card.match_keys[0]
    header_title = title or f"MX 多规格上架审批 · {keys_label}"
    md = group_confirm_card_markdown(card)
    if risk_note:
        md = f"**审批说明** · {risk_note}\n\n---\n\n{md}"

    elements: list[dict[str, Any]] = []
    elements.extend(_image_elements(card.main_image_url))
    elements.append({"tag": "div", "text": {"tag": "lark_md", "content": md[:8000]}})
    elements.append({"tag": "hr"})
    elements.append(
        _approval_form_block(
            task_id=task_id,
            title=header_title,
            confirm_token=card.token,
            match_key=card.match_keys[0] if card.match_keys else None,
        )
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": header_title[:100]},
        },
        "elements": elements,
    }


def build_batch_mx_approval_card(
    cards: list[MxConfirmCard],
    *,
    task_id: str,
    title: str,
    risk_note: str = "",
) -> dict[str, Any]:
    """批量审批：汇总表 + 各 SKU 完整价格块（与对话框一致）。"""
    summary_lines = [
        f"**{title}**",
        f"任务 `{task_id}` · 共 **{len(cards)}** 个 SKU",
        "",
        "| 对齐码 | 上传原价 MXN | POP折后 | 净利 | 尺寸 |",
        "|--------|-------------|---------|------|------|",
    ]
    for c in cards:
        summary_lines.append(
            f"| {c.match_key} | **{c.list_price_ceil_mxn}** | {c.sale_price_mxn:.0f} | "
            f"{c.net_profit_mxn:.0f} | {c.package_cm} |"
        )
    if risk_note:
        summary_lines.extend(["", f"**审批说明** · {risk_note}"])

    elements: list[dict[str, Any]] = [
        {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(summary_lines)}},
        {"tag": "hr"},
    ]
    for i, card in enumerate(cards):
        if i > 0:
            elements.append({"tag": "hr"})
        elements.extend(_image_elements(card.main_image_url))
        body = confirm_card_markdown(card)
        # 批量模式下缩短标题行避免重复 header
        body = body.replace(f"## MX 上架确认 · {card.match_key}", f"### {card.match_key} · `{card.seller_sku}`")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": body[:6000]}})

    elements.append({"tag": "hr"})
    elements.append(
        _approval_form_block(
            task_id=task_id,
            title=title,
            match_key=cards[0].match_key if cards else None,
        )
    )
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "template": "orange",
            "title": {"tag": "plain_text", "content": title[:100]},
        },
        "elements": elements,
    }


def default_chat_id() -> str:
    return (
        os.environ.get("FEISHU_DEFAULT_CHAT_ID", "").strip()
        or "oc_98de01670b5de146734f7530e0a1f83c"
    )


def send_mx_approval_card(
    interactive: dict[str, Any],
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    from modules.hub.feishu_app import send_interactive_card

    cid = chat_id or default_chat_id()
    return send_interactive_card(cid, interactive)
