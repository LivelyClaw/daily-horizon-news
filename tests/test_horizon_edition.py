from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

from scripts import horizon_edition as edition


def article(i, title, score, source="rss · IT之家", content=None):
    return (f'<a id="item-tech-news-{i}"></a>\n'
            f'### [{title}](https://news.example.org/{i}) ⭐️ {score}/10\n\n'
            f'{content or title}。\n\n{source} · 11月24日 09:30\n\n'
            f'<details><summary>参考链接</summary>资料{i}</details>\n\n---\n\n')


class EditorialTest(unittest.TestCase):
    def setUp(self):
        self.titles = ["董事会恢复 CEO 职务", "另一家公司的任命", "各方庆祝 CEO 复职",
                       "国会回应这次复职", "复职后的董事会表态", "同一公司推出芯片"]
        reports = [article(i, title, score, content=("长文" * 7000) if i == 1 else None)
                   for i, (title, score) in enumerate(zip(self.titles, [9, 8.5, 8, 7, 7, 8]), 1)]
        self.raw = "# Horizon 每日速递\n\n## 科技新闻\n\n" + "".join(reports[:3]) + "## 博客\n\n" + "".join(reports[3:])
        self.stories = edition.read_stories(self.raw)
        self.plan = {"events": [[5, 4, 1, 3], [6], [2]], "items": [
            {"id": i, "sentence": title + "。", "evidence": "secondary" if i == 1 else "primary", "reach": "wide"}
            for i, title in enumerate(self.titles, 1)]}

    def test_cross_profile_event_keeps_top_score_and_all_of_its_markdown(self):
        with patch.object(edition, "editorial_plan", new=AsyncMock(return_value=self.plan)):
            preview, document = edition.prepare(self.raw, "2026-11-24")
        self.assertIn("共 3 个事件", preview)
        self.assertEqual(preview.count("（来源：IT之家）"), 3)
        self.assertIn("长文" * 7000, document)
        self.assertIn("<details><summary>参考链接</summary>资料1</details>", document)
        self.assertNotIn("## 博客", document)
        for i in (1, 2, 6):
            self.assertIn(f"https://news.example.org/{i}", document)
        for i in (3, 4, 5):
            self.assertNotIn(self.titles[i-1], preview + document)

    def test_ties_compare_reliability_then_reach_then_original_order(self):
        for story in self.stories:
            story.score = 9
        self.plan["events"] = [[6, 5, 4, 3, 2, 1]]
        items = self.plan["items"]
        for item in items:
            item["reach"] = "specialist"
        items[0]["reach"] = "wide"    # More popular, but a secondary source.
        items[2]["reach"] = "wide"
        items[3]["reach"] = "wide"    # Same trust/reach, later in the original.
        _, doc = edition.render(self.stories, self.plan, "2026-11-24")
        self.assertIn("https://news.example.org/3", doc)
        self.assertEqual(doc.count("\n## "), 1)

    def test_preview_lists_every_attachment_event_in_order_with_source(self):
        raw = "".join(article(i, f"报道{i}", 9,
                              "hackernews · account" if i == 1 else "rss · 阮一峰")
                      for i in range(1, 8))
        stories = edition.read_stories(raw)
        plan = {"events": [[i] for i in range(1, 8)], "items": [
            dict(id=i, sentence=f"简讯{i}。", evidence="unknown", reach="unknown") for i in range(1, 8)]}
        plan["items"][0]["sentence"] = "原文称“开始实施。”第二句是背景。"
        preview, doc = edition.render(stories, plan, "2026-11-24")
        expected = ["原文称“开始实施。”（来源：news.example.org · Hacker News）"]
        expected.extend(f"简讯{i}。（来源：阮一峰）" for i in range(2, 8))
        self.assertEqual(preview.splitlines()[1:-1],
                         [f"{i}. {line}" for i, line in enumerate(expected, 1)])
        self.assertEqual([line for line in doc.splitlines() if "（来源：" in line], expected)

    def test_rejects_incomplete_or_overlapping_groups(self):
        for events in ([[1, 3, 4, 5], [2]], [[1, 3, 4, 5], [1, 6], [2]]):
            plan = deepcopy(self.plan)
            plan["events"] = events
            with self.assertRaises(ValueError):
                edition.render(self.stories, plan, "2026-11-24")

    def test_empty_edition_and_unreadable_heading(self):
        raw = "# Horizon 每日速递\n\n已分析 12 条内容，但没有达到重要性阈值的条目。"
        preview, doc = edition.prepare(raw, "2026-11-24")
        self.assertIn("没有达到重要性阈值", preview)
        self.assertEqual(doc, raw)
        changed = self.raw.replace(" ⭐️ 9/10", " 格式改变", 1)
        preview, doc = edition.prepare(changed, "2026-11-24")
        self.assertIn("原始 Markdown", preview)
        self.assertEqual(doc, changed)
