"""Hub CLI：日报预览与飞书推送。"""

from __future__ import annotations

from modules.hub import digest as digest_mod
from modules.hub.feishu import feishu_config, send_post


def preview_digest() -> None:
    print(digest_mod.preview_text())


def send_digest(*, dry_run: bool = False) -> None:
    cfg = feishu_config()
    if not cfg["enabled"] and not dry_run:
        raise RuntimeError(
            "飞书未启用。请在 config/settings.json 设置 feishu.enabled=true 并填写 webhook_url"
        )
    snap = digest_mod.collect_snapshot()
    path = digest_mod.save_digest_log(snap)
    title, rows = digest_mod.build_feishu_post(snap)
    print(digest_mod.preview_text())
    print(f"\n已保存快照: {path}")
    if dry_run:
        print("（dry-run，未发送飞书）")
        return
    send_post(title, rows)
    print("✅ 已发送到飞书")
