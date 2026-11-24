import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from scripts import horizon_deliver_zh as delivery


SCRIPT = Path(delivery.__file__).resolve()
FAKE_HERMES = """#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
root = Path(__file__).resolve().parent
message = sys.stdin.read()
receipt = root / "receipt"
if message.startswith('MEDIA:"') and message.endswith('"'):
    path = Path(message[7:-1])
    message = {"name": path.name, "body": path.read_text()}
    receipt = root / "file_receipt" if (root / "file_receipt").exists() else receipt
with (root / "attempts").open("a") as log:
    print(json.dumps([sys.argv[1:], message, os.environ["HERMES_HOME"]]), file=log)
if (root / "block").exists():
    deadline = time.monotonic() + 10
    while (root / "block").exists() and time.monotonic() < deadline:
        time.sleep(0.02)
print(receipt.read_text())
sys.exit(int(os.environ.get("SEND_EXIT", "0")))
"""


class DeliveryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.data = self.home / "Horizon/data"
        self.summaries = self.data / "summaries"
        self.summaries.mkdir(parents=True)
        venv_bin = self.data.parent / ".venv/bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python").symlink_to(sys.executable)
        hermes = self.home / "hermes"
        hermes.write_text(FAKE_HERMES)
        hermes.chmod(0o755)
        self.profile = self.home / "hermes profile"
        self.env = {**os.environ, "HOME": str(self.home), "HERMES_HOME": str(self.profile),
                    "PATH": str(self.home) + os.pathsep + os.environ["PATH"]}
        self.today = datetime.now(timezone.utc).date()
        self.success()

    def success(self):
        (self.home / "receipt").write_text('{"success":true,"message_id":"om_test"}')

    def run_script(self, *args, ok=True):
        result = subprocess.run([sys.executable, SCRIPT, *args], env=self.env,
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode == 0, ok, result.stderr)
        self.assertEqual(result.stdout, "")
        return result

    def configure(self):
        self.run_script("--configure", "feishu:oc_test")

    def report(self, body, day=None):
        path = self.summaries / f"horizon-{day or self.today}-zh.md"
        path.write_text(body, encoding="utf-8")
        return path

    def attempts(self):
        log = self.home / "attempts"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def file_bodies(self):
        return [msg["body"] for _, msg, _ in self.attempts() if isinstance(msg, dict)]

    def test_retry_retains_both_versions_and_confirms_only_once(self):
        self.configure()
        first = self.report("早报")
        first_time = first.stat().st_mtime_ns
        (self.home / "receipt").write_text('{"error":"unavailable"}')
        self.env["SEND_EXIT"] = "1"
        self.run_script(ok=False)
        second = self.report("晚报")
        os.utime(second, ns=(first_time, first_time))
        self.run_script("--enqueue-only")
        self.success()
        self.env.pop("SEND_EXIT")
        self.env.pop("HERMES_HOME")  # cron receives the profile saved at configuration
        self.run_script()
        self.run_script()
        self.run_script()
        calls = self.attempts()
        self.assertEqual(len(calls), 5)
        self.assertEqual(calls[0][1], calls[1][1])
        self.assertEqual(self.file_bodies(), ["早报", "晚报"])
        self.assertNotEqual(calls[2][1]["name"], calls[4][1]["name"])
        for args, _, profile in calls:
            self.assertEqual(args, ["send", "--to", "feishu:oc_test", "--file", "-", "--json"])
            self.assertEqual(profile, str(self.profile.resolve()))
        with closing(sqlite3.connect(self.data / ".delivery_zh.sqlite3")) as db:
            rows = db.execute("SELECT body, message_id FROM editions").fetchall()
        self.assertEqual(rows, [(None, "om_test"), (None, "om_test")])

    def test_skipped_partial_invalid_and_nonzero_receipts_stay_pending(self):
        self.report("等待确认")
        self.configure()
        for response, code in [('{"success":true,"skipped":true}', "0"),
                               ('{"success":true,"partial_success":true}', "0"),
                               ("not JSON", "0"), ("[]", "0"), ("{}", "0"),
                               ('{"success":true}', "1")]:
            with self.subTest(response=response, code=code):
                (self.home / "receipt").write_text(response)
                self.env["SEND_EXIT"] = code
                self.run_script(ok=False)
        self.env.pop("SEND_EXIT")
        self.success()
        self.run_script()
        self.run_script()
        self.assertEqual(len(self.attempts()), 8)
        self.assertEqual(self.file_bodies(), ["等待确认"])

    def test_concurrent_poll_and_generation(self):
        self.report("第一期")
        self.configure()
        block = self.home / "block"
        block.touch()
        with subprocess.Popen([sys.executable, SCRIPT], env=self.env) as first:
            try:
                deadline = time.monotonic() + 3
                while not self.attempts() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual(len(self.attempts()), 1)
                self.run_script()
                self.report("第二期")
                self.run_script("--enqueue-only")
                self.run_script()
                self.assertEqual(len(self.attempts()), 1)
            finally:
                block.unlink()
            self.assertEqual(first.wait(timeout=5), 0)
        self.run_script()
        self.assertEqual(self.file_bodies(), ["第一期", "第二期"])

    def test_beijing_overnight_and_multiday_catchup(self):
        instant = datetime.fromisoformat("2026-11-24T01:00:00+08:00")
        utc_day = date(2026, 11, 23)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant.astimezone(tz)

        with patch.object(delivery, "datetime", FixedClock), patch.object(delivery, "DATA_DIR", self.data), \
             patch.dict(os.environ, self.env):
            self.report("此前的存档", utc_day - timedelta(days=1))
            self.report("凌晨补跑", utc_day)
            # Call the public entry point with a fixed clock and restore its umask.
            previous_mask = os.umask(0o077)
            try:
                with patch.object(sys, "argv", [str(SCRIPT), "--configure", "feishu:oc_test"]):
                    delivery.main()
                self.assertEqual(self.attempts(), [])
                with patch.object(sys, "argv", [str(SCRIPT)]):
                    delivery.main()
            finally:
                os.umask(previous_mask)
        with closing(delivery.open_outbox(self.data)) as db:
            self.report("停机期间", utc_day + timedelta(days=2))
            delivery.capture(db, self.data, utc_day + timedelta(days=4))
            delivery.send_one(db, self.data)
        self.assertEqual(self.file_bodies(), ["凌晨补跑", "停机期间"])

    def test_upgrade_confirms_current_version_and_leaves_archive(self):
        self.report("归档", self.today - timedelta(days=1))
        current = self.report("当前版本")
        legacy = self.data / ".delivery_state_zh.json"
        marker = json.dumps({"date": self.today.isoformat(), "mtime": int(current.stat().st_mtime)})
        legacy.write_text(marker)
        self.configure()
        self.run_script()
        self.configure()
        self.run_script()
        self.assertEqual(self.file_bodies(), ["当前版本"])
        self.assertEqual(legacy.read_text(), marker)

    def test_empty_body_and_missing_target(self):
        self.report("<details>参考</details>")
        self.run_script(ok=False)
        self.run_script("--configure", "feishu:oc_REPLACE", ok=False)
        self.configure()
        self.run_script()
        self.assertEqual(self.attempts(), [])

    def test_attachment_retry_keeps_full_snapshot_and_only_resends_file(self):
        body = "本期全文" * 5000
        self.report(body)
        self.configure()
        (self.home / "file_receipt").write_text('{"success":true,"media_delivered":false}')
        self.run_script(ok=False)
        self.report("下一期")
        (self.home / "file_receipt").unlink()
        self.run_script()
        self.assertEqual(len(self.attempts()), 3)
        self.assertEqual(self.file_bodies(), [body, body])
        self.assertLess(len(self.attempts()[0][1]), 120)

    def test_old_database_keeps_pending_rows_and_does_not_resend_confirmed(self):
        import hashlib
        with closing(sqlite3.connect(self.data / ".delivery_zh.sqlite3")) as db, db:
            db.execute("""CREATE TABLE editions (day TEXT, digest BLOB, body TEXT,
                          message_id TEXT, UNIQUE(day,digest))""")
            db.execute("INSERT INTO editions VALUES (?, ?, NULL, 'sent')",
                       (str(self.today), hashlib.sha256(b"yesterday").digest()))
            db.execute("INSERT INTO editions VALUES (?, ?, ?, NULL)",
                       (str(self.today), hashlib.sha256(b"lost").digest(), "……（已截断。完整版见旧目录）"))
        self.configure()
        self.run_script()
        self.assertIn("节选", self.attempts()[0][1])
        self.assertEqual(len(self.file_bodies()), 1)


if __name__ == "__main__":
    unittest.main()
