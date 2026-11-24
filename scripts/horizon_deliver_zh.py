#!/usr/bin/env python3
"""Horizon 中文日报投递看门狗。

Hermes cron 每 5 分钟调用脚本。脚本输出当天新增或更新的中文日报，
并记录已输出的版本。Hermes 将输出发送到配置好的会话。
"""
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

DATA_DIR = Path.home() / "Horizon" / "data"
SUMMARY_DIR = DATA_DIR / "summaries"
MARKER = DATA_DIR / ".delivery_state_zh.json"
CST = timezone(timedelta(hours=8))
MAX_CHARS = 12000


def today_cst() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d")


def clean(text: str) -> str:
    # 去掉 <details> 折叠块(飞书纯文本里无法展开)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    # 锚点链接 [标题](#anchor) -> 标题
    text = re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", text)
    # 压缩 3 个以上连续空行
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    cut = text.rfind("\n\n", 0, MAX_CHARS)
    cut = cut if cut > 0 else MAX_CHARS
    return text[:cut] + "\n\n……（本期内容较长，已截断。完整版见 ~/Horizon/data/summaries/）"


def main() -> None:
    date = today_cst()
    summary = SUMMARY_DIR / f"horizon-{date}-zh.md"
    if not summary.exists():
        return
    mtime = int(summary.stat().st_mtime)
    delivered = None
    if MARKER.exists():
        try:
            delivered = json.loads(MARKER.read_text())
        except Exception:
            delivered = None
    if delivered and delivered.get("date") == date and delivered.get("mtime") == mtime:
        return  # 本期已投递
    content = truncate(clean(summary.read_text(encoding="utf-8")))
    if not content:
        return
    print(content)
    MARKER.write_text(json.dumps({"date": date, "mtime": mtime}), encoding="utf-8")


if __name__ == "__main__":
    main()
