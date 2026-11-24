#!/usr/bin/env python3
"""保存 Horizon 中文日报快照，通过 Hermes 发送并确认。"""
import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path.home() / "Horizon" / "data"
MAX_CHARS = 12000


def clean(text: str) -> str:
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S)
    text = re.sub(r'<a id="[^"]*"></a>', "", text)
    text = re.sub(r"\[([^\]]+)\]\(#[^)]*\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Hermes 把 MEDIA: 解析为本地附件指令，日报只发送正文。
    return re.sub(r"MEDIA:", "MEDIA：", text, flags=re.I).strip()


def truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    notice = "\n\n……（本期内容较长，已截断。完整版见 ~/Horizon/data/summaries/）"
    limit = MAX_CHARS - len(notice)
    cut = text.rfind("\n\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut] + notice


def open_outbox(data_dir: Path) -> sqlite3.Connection:
    db = sqlite3.connect(data_dir / ".delivery_zh.sqlite3", timeout=30)
    db.execute("""CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY CHECK (id = 1), target TEXT NOT NULL,
        hermes TEXT NOT NULL, profile TEXT NOT NULL, scan_from TEXT NOT NULL
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS editions (
        day TEXT NOT NULL, digest BLOB NOT NULL, body TEXT, message_id TEXT,
        UNIQUE (day, digest)
    )""")
    return db


def configure(db: sqlite3.Connection, target: str, today: date) -> None:
    if not re.fullmatch(r"feishu:[^\s]+", target) or target == "feishu:oc_REPLACE":
        raise ValueError("请填写实际飞书会话，例如 feishu:oc_…")
    hermes = shutil.which("hermes")
    if not hermes:
        raise ValueError("找不到 hermes，请在运行 Hermes 的用户环境中配置")
    profile = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
    with db:
        db.execute("""INSERT INTO settings VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET target=excluded.target,
                hermes=excluded.hermes, profile=excluded.profile""",
            (target, os.path.abspath(hermes), str(profile.resolve()), today.isoformat()))


def capture(db: sqlite3.Connection, data_dir: Path, today: date) -> None:
    # 在同一个事务内读取上游原子替换的文件、保存快照并推进扫描日期。
    with db:
        db.execute("BEGIN IMMEDIATE")
        settings = db.execute("SELECT scan_from FROM settings").fetchone()
        if settings is None:
            raise ValueError("请先用 --configure 配置 Hermes 飞书会话")
        first = date.fromisoformat(settings[0])
        day = first
        while day <= today:
            path = data_dir / "summaries" / f"horizon-{day}-zh.md"
            try:
                raw = path.read_bytes()
            except FileNotFoundError:
                pass
            else:
                body = truncate(clean(raw.decode("utf-8")))
                if body:
                    db.execute("""INSERT INTO editions (day, digest, body) VALUES (?, ?, ?)
                        ON CONFLICT(day, digest) DO NOTHING""",
                        (day.isoformat(), hashlib.sha256(raw).digest(), body))
            day += timedelta(days=1)
        # Horizon 确定文件名和写入完成之间可能跨过 UTC 零点。
        scan_from = max(first, today - timedelta(days=1))
        db.execute("UPDATE settings SET scan_from = ?", (scan_from.isoformat(),))


def send_one(db: sqlite3.Connection, data_dir: Path) -> None:
    # 发送期间只锁发送者，生成服务仍能保存新一期正文。
    with (data_dir / ".delivery_zh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        edition = db.execute("""SELECT rowid, day, body FROM editions
            WHERE body IS NOT NULL ORDER BY rowid LIMIT 1""").fetchone()
        if edition is None:
            return
        row_id, day, body = edition
        target, hermes, profile = db.execute("SELECT target, hermes, profile FROM settings").fetchone()
        try:
            result = subprocess.run(
                [hermes, "send", "--to", target, "--file", "-", "--json"],
                input=body, capture_output=True, encoding="utf-8", timeout=90,
                env={**os.environ, "HERMES_HOME": profile},
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"{day} 等待发送回执超时，已保留待发正文") from exc
        try:
            receipt = json.loads(result.stdout)
        except ValueError:
            receipt = None
        if not isinstance(receipt, dict):
            raise ValueError(f"{day} 未取得 Hermes JSON 回执，退出码 {result.returncode}，已保留待发正文")
        if (
            result.returncode or receipt.get("success") is not True
            or receipt.get("skipped") or receipt.get("error") or receipt.get("partial_success")
        ):
            reason = receipt.get("error") or receipt.get("reason") or f"退出码 {result.returncode}"
            raise ValueError(f"{day} 投递未确认，已保留待发正文。{reason}")
        with db:
            db.execute("UPDATE editions SET body = NULL, message_id = ? WHERE rowid = ?",
                       (receipt.get("message_id"), row_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configure", metavar="feishu:CHAT_ID", help="记录发送目标和当前 Hermes 配置")
    mode.add_argument("--enqueue-only", action="store_true", help="仅保存生成的日报")
    args = parser.parse_args()
    os.umask(0o077)
    today = datetime.now(timezone.utc).date()
    with closing(open_outbox(DATA_DIR)) as db:
        if args.configure is not None:
            configure(db, args.configure, today)
        capture(db, DATA_DIR, today)
        if args.configure is None and not args.enqueue_only:
            send_one(db, DATA_DIR)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.exit(str(exc))
