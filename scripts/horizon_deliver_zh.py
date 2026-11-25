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
import tempfile
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATA_DIR = Path.home() / "Horizon" / "data"


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
    with db:
        db.execute("BEGIN IMMEDIATE")
        columns = {row[1] for row in db.execute("PRAGMA table_info(editions)")}
        if "phase" not in columns:
            db.execute("ALTER TABLE editions ADD COLUMN phase TEXT NOT NULL DEFAULT 'legacy'")
            db.execute("ALTER TABLE editions ADD COLUMN attachment TEXT")
            db.execute("ALTER TABLE editions ADD COLUMN preview_route TEXT")
        if "edition_name" not in columns:
            db.execute("ALTER TABLE editions ADD COLUMN edition_name TEXT")
        if "topics_json" not in columns:
            db.execute("ALTER TABLE editions ADD COLUMN topics_json TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS doc_folder (id INTEGER PRIMARY KEY CHECK(id=1), token TEXT NOT NULL)")
        db.execute("""CREATE TABLE IF NOT EXISTS publications (
            edition INTEGER PRIMARY KEY, title TEXT NOT NULL UNIQUE, ticket TEXT, token TEXT, url TEXT,
            route TEXT, next_page INTEGER NOT NULL DEFAULT 0)""")
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


def document_sender():
    if __package__:
        from . import horizon_docs
    else:
        from pathlib import Path
        # systemd 可从符号链接启动脚本；Hermes 也可以不将脚本目录加入导入路径。
        directory = str(Path(__file__).resolve().parent)
        if directory not in sys.path:
            sys.path.insert(0, directory)
        import horizon_docs
    return horizon_docs


def configure_webhook(db, today):
    profile = Path.home() / ".hermes"
    document_sender().configure_folder(db, profile / ".env")
    with db:
        db.execute("""INSERT INTO settings (id,target,hermes,profile,scan_from) VALUES (1,'webhook-docs','',?,?)
            ON CONFLICT(id) DO UPDATE SET target=excluded.target,hermes='',profile=excluded.profile""",
            (str(profile), today.isoformat()))


def edition_title(db, day, modified, digest):
    updated = datetime.fromtimestamp(modified, timezone.utc)
    if updated.date() != day:
        return f"{day} 日报｜Horizon 中文日报（{digest.hex()[:8]}）"
    slot = "早报" if updated.hour < 12 else "晚报"
    title = f"{day} {slot}｜Horizon 中文日报"
    if db.execute("SELECT 1 FROM editions WHERE edition_name=? AND digest!=?", (title, digest)).fetchone():
        title += f"（{digest.hex()[:8]}）"
    return title


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
                with path.open("rb") as stream:
                    raw = stream.read()
                    modified = os.fstat(stream.fileno()).st_mtime
            except FileNotFoundError:
                pass
            else:
                body = raw.decode("utf-8")
                if re.sub(r"<details>.*?</details>", "", body, flags=re.S).strip():
                    digest = hashlib.sha256(raw).digest()
                    title = edition_title(db, day, modified, digest)
                    db.execute("""INSERT INTO editions (day, digest, body, phase, edition_name)
                        VALUES (?, ?, ?, 'raw', ?) ON CONFLICT(day,digest) DO UPDATE SET
                        edition_name=excluded.edition_name WHERE editions.edition_name IS NULL""",
                        (day.isoformat(), digest, body, title))
            day += timedelta(days=1)
        # Horizon 确定文件名和写入完成之间可能跨过 UTC 零点。
        scan_from = max(first, today - timedelta(days=1))
        db.execute("UPDATE settings SET scan_from = ?", (scan_from.isoformat(),))


def send_message(hermes: str, target: str, profile: str, message: str, day: str) -> dict:
    try:
        result = subprocess.run(
            [hermes, "send", "--to", target, "--file", "-", "--json"],
            input=message, capture_output=True, encoding="utf-8", timeout=90,
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
        or receipt.get("media_dropped") or receipt.get("skipped") or receipt.get("error") or receipt.get("partial_success")
    ):
        reason = receipt.get("error") or receipt.get("reason") or f"退出码 {result.returncode}"
        raise ValueError(f"{day} 投递未确认，已保留待发正文。{reason}")
    return receipt


def prepare_edition(data_dir: Path, day: str, raw: str) -> dict:
    root = data_dir.parent
    editor = Path(__file__).resolve().with_name("horizon_edition.py")
    try:
        process = subprocess.run(
            [root / ".venv/bin/python", editor, day], cwd=root, input=raw,
            capture_output=True, encoding="utf-8", timeout=240, check=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{day} 编辑超时，原稿已入队，稍后重试") from exc
    except subprocess.CalledProcessError as exc:
        # 模型/API 的异常可能含请求信息，不把原始输出写入群消息或日志。
        raise ValueError(f"{day} 编辑失败，原稿仍在队列中（退出码 {exc.returncode}）") from exc
    return json.loads(process.stdout)


def send_one(db: sqlite3.Connection, data_dir: Path) -> None:
    # 发送期间只锁发送者，生成服务仍能保存新一期正文。
    with (data_dir / ".delivery_zh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        edition = db.execute("""SELECT rowid, day, digest, body, phase, attachment, preview_route, topics_json FROM editions
            WHERE body IS NOT NULL ORDER BY rowid LIMIT 1""").fetchone()
        if edition is None:
            return
        row_id, day, digest, body, phase, attachment, preview_route, topics_json = edition
        target, hermes, profile = db.execute("SELECT target, hermes, profile FROM settings").fetchone()
        if phase == "legacy":
            # 旧脚本只保留过裁剪后的正文。仅当摘要相符时才用磁盘原稿恢复它。
            original = data_dir / "summaries" / f"horizon-{day}-zh.md"
            try:
                raw = original.read_bytes()
            except FileNotFoundError:
                raw = None
            if raw is not None and hashlib.sha256(raw).digest() == digest:
                body = raw.decode("utf-8")
        if phase in {"legacy", "raw"}:
            edited = prepare_edition(data_dir, day, body)
            body, attachment = edited["preview"], edited["markdown"]
            topics_json = json.dumps(edited.get("topics", []), ensure_ascii=False)
            with db:
                db.execute("UPDATE editions SET body=?, attachment=?, topics_json=?, phase='preview' WHERE rowid=?",
                           (body, attachment, topics_json, row_id))
            phase = "preview"
        if target == "webhook-docs":
            topics = json.loads(topics_json) if topics_json else None
            document_sender().publish(db, data_dir, row_id, day, digest, body, attachment,
                                      Path(profile) / ".env", topics=topics)
            return
        route = json.dumps([profile, target])
        if phase == "preview" or (phase == "file" and preview_route != route):
            # 只有下面由程序生成的本地路径可以作为 Hermes 附件指令。
            preview = re.sub(r"MEDIA:", "MEDIA：", body, flags=re.I)
            send_message(hermes, target, profile, preview, day)
            with db:
                db.execute("UPDATE editions SET phase='file', preview_route=? WHERE rowid=?", (route, row_id))
        cache = Path(profile) / "cache/documents"
        cache.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="horizon-", dir=cache) as folder:
            path = Path(folder) / f"Horizon-{day}-{digest.hex()[:12]}.md"
            path.write_text(attachment, encoding="utf-8")
            # Hermes 支持加引号的绝对路径（包括空格）。
            if any(c in str(path) for c in ('"', "\n", "\r")):
                raise ValueError("Hermes 附件路径包含不支持的字符")
            receipt = send_message(hermes, target, profile, f'MEDIA:"{path}"', day)
            if receipt.get("warnings") or receipt.get("media_delivered") is False:
                raise ValueError(f"{day} Markdown 附件投递未确认，将单独重试")
        with db:
            db.execute("""UPDATE editions SET body=NULL, attachment=NULL, phase='done', message_id=?
                          WHERE rowid=?""", (receipt.get("message_id"), row_id))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--configure", metavar="feishu:CHAT_ID", help="记录发送目标和当前 Hermes 配置")
    mode.add_argument("--enqueue-only", action="store_true", help="仅保存生成的日报")
    mode.add_argument("--configure-webhook", action="store_true", help="建立飞书日报文件夹，启用签名 Webhook")
    args = parser.parse_args()
    os.umask(0o077)
    today = datetime.now(timezone.utc).date()
    with closing(open_outbox(DATA_DIR)) as db:
        if args.configure is not None:
            configure(db, args.configure, today)
        elif args.configure_webhook:
            configure_webhook(db, today)
        capture(db, DATA_DIR, today)
        if args.configure is None and not args.configure_webhook and not args.enqueue_only:
            send_one(db, DATA_DIR)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, sqlite3.Error) as exc:
        sys.exit(str(exc))
