import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import horizon_deliver_zh as delivery
from scripts import horizon_docs as docs
from scripts.horizon_webhook import FeishuWebhook, MAX_BODY_BYTES


class PublicationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        self.db = delivery.open_outbox(self.data)
        self.addCleanup(self.db.close)
        self.title = "2026-11-25 早报｜Horizon 中文日报"
        self.url = "https://my.feishu.cn/docx/test"
        self.digest = hashlib.sha256(b"edition").digest()
        self.db.execute("INSERT INTO doc_folder VALUES (1,'archive')")
        self.db.execute("""INSERT INTO editions (day,digest,body,attachment,phase,edition_name)
                        VALUES ('2026-11-25',?,'preview','markdown','preview',?)""", (self.digest,self.title))
        self.drive = Mock()
        self.drive.upload.return_value = "upload"
        self.drive.request.side_effect = [
            {"ticket": "ticket"}, {"result": {"job_status": 0, "token": "doc", "url": self.url, "extra": ["_pod_name"]}}]
        self.hook = Mock(url="test-hook")
        self.hook_patch = patch.object(docs.FeishuWebhook, "from_file", return_value=self.hook)
        self.drive_patch = patch.object(docs, "Drive", return_value=self.drive)
        self.hook_patch.start()
        self.drive_patch.start()
        self.addCleanup(self.hook_patch.stop)
        self.addCleanup(self.drive_patch.stop)

    def publish(self, preview, topics=None):
        docs.publish(self.db, self.data, 1, "2026-11-25", self.digest, preview, "markdown", self.data/".env",
                     topics=topics)

    def test_expanded_topics_show_every_event_and_both_links(self):
        topics = [{"name": name, "events": [
            dict(number=i, before="研究团队", focus="称观察到新结果", after="，仍待验证。",
                 source="A&B <example>")]} for i, name in enumerate(["AI 研究与评测", "开发者观察"], 1)]
        self.publish("本期速览", topics)
        self.hook.send.assert_called_once()
        elements = self.hook.send.call_args.args[0]["card"]["elements"]
        self.assertEqual([e["tag"] for e in elements], ["div", "markdown", "hr", "markdown", "action"])
        self.assertEqual(elements[0]["text"]["content"], "2 个事件 · 2 个主题")
        for i, group in enumerate(topics, 1):
            content = elements[2*i-1]["content"]
            self.assertIn(f"**{group['name']}**", content)
            self.assertIn(f">{i:02}</font>  研究团队**称观察到新结果**，仍待验证。", content)
            self.assertIn("（A&#38;B &#60;example&#62;）</font>", content)
        self.assertEqual([b["url"] for b in elements[-1]["actions"]],
                         [self.url, "https://my.feishu.cn/drive/folder/archive"])

    def test_paginated_topics_keep_numbers_and_respect_signed_limit(self):
        topics = [{"name": "开发者观察", "events": [
            dict(number=i, before="", focus=f"报道{i}", after="。", source="来源" * 60)
            for i in range(1, 201)]}]
        pages = list(docs.cards(self.title, "", self.url, "https://my.feishu.cn/drive/folder/archive", topics))
        self.assertGreater(len(pages), 1)
        expected = iter(range(1, 201))
        for i, page in enumerate(pages, 1):
            self.assertLessEqual(len(FeishuWebhook("unused", "test").signed_body(page, 1599360473)), MAX_BODY_BYTES)
            elements = page["card"]["elements"]
            self.assertEqual(elements[-1]["tag"] == "action", i == len(pages))
            lines = elements[1]["content"].split("\n\n")
            self.assertIn("**开发者观察**", lines[0])
            for line in lines[1:]:
                number = next(expected)
                self.assertIn(f">{number:02}</font>  **报道{number}**", line)
        self.assertIsNone(next(expected, None))

    def test_retry_reuses_document_and_resumes_card_pages(self):
        preview = "Horizon 中文日报 · 2026-11-25｜共 400 个事件\n" + "\n".join(
            f"{i}. " + "新闻" * 15 + "（来源：日报）" for i in range(1,401))
        self.hook.send.side_effect = [None, ValueError("发送失败")]
        with self.assertRaises(ValueError):
            self.publish(preview)
        self.hook.send.side_effect = None
        self.publish(preview)
        self.drive.upload.assert_called_once()
        self.assertEqual(self.drive.request.call_args_list[0].kwargs['json']['file_name'], self.title)
        self.assertEqual(self.drive.request.call_args_list[0].kwargs['json']['point']['mount_key'], 'archive')
        messages = [c.args[0] for c in self.hook.send.call_args_list]
        self.assertEqual(messages[1],messages[2])
        successful = [messages[0],*messages[2:]]
        seen = []
        for card in successful:
            self.assertLessEqual(len(FeishuWebhook("unused", "test").signed_body(card,1599360473)), MAX_BODY_BYTES)
            seen.extend(card["card"]["elements"][0]["text"]["content"].splitlines())
        self.assertEqual(seen[1:], preview.splitlines()[1:])
        buttons=successful[-1]["card"]["elements"][-1]["actions"]
        self.assertEqual([b["url"] for b in buttons], [self.url, "https://my.feishu.cn/drive/folder/archive"])
        self.assertEqual(self.db.execute("SELECT phase,body FROM editions").fetchone(), ("done",None))
        self.assertEqual((self.data/'editions'/(self.title+'.md')).read_text(),'markdown')

    def test_no_card_when_sharing_is_unconfirmed(self):
        self.drive.public_read.side_effect=[None,ValueError("分享未确认")]
        with self.assertRaises(ValueError):
            self.publish("速览")
        self.hook.send.assert_not_called()
        self.assertEqual(self.db.execute("SELECT url FROM publications").fetchone()[0],self.url)

    def test_uncertain_import_is_recovered_in_correct_folder(self):
        self.drive.request.side_effect=ValueError("网络异常")
        with self.assertRaises(ValueError):
            self.publish("速览")
        self.drive.find.return_value={"token":"doc","url":self.url}
        self.publish("速览")
        self.drive.upload.assert_called_once()
        self.drive.find.assert_called_once_with('archive',self.title,'docx')

    def test_truncated_import_is_not_published(self):
        self.drive.request.side_effect=[{'ticket':'ticket'},{'result':{'job_status':0,'extra':['1000']}}]
        with self.assertRaisesRegex(ValueError,'不完整'):
            self.publish('速览')
        self.hook.send.assert_not_called()
