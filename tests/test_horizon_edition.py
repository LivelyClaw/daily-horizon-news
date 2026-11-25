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
            {"id": i, "topic": "公司动态", "sentence": f"**{title}**。",
             "evidence": "secondary" if i == 1 else "primary", "reach": "wide"}
            for i, title in enumerate(self.titles, 1)]}

    def test_cross_profile_event_keeps_top_score_and_all_of_its_markdown(self):
        with patch.object(edition, "editorial_plan", new=AsyncMock(return_value=self.plan)):
            result = edition.prepare(self.raw, "2026-11-24")
        preview, document = result.preview, result.markdown
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
        doc = edition.render(self.stories, self.plan, "2026-11-24").markdown
        self.assertIn("https://news.example.org/3", doc)
        self.assertEqual(doc.count("\n### "), 1)

    def test_preview_lists_every_attachment_event_in_order_with_source(self):
        raw = "".join(article(i, f"报道{i}", 9,
                              "hackernews · account" if i == 1 else "rss · 阮一峰")
                      for i in range(1, 8))
        stories = edition.read_stories(raw)
        plan = {"events": [[i] for i in range(1, 8)], "items": [
            dict(id=i, topic="开发者观察", sentence=f"**简讯{i}**。", evidence="unknown", reach="unknown")
            for i in range(1, 8)]}
        plan["items"][0]["sentence"] = "**作者称测试已通过**，结果尚待独立验证。"
        result = edition.render(stories, plan, "2026-11-24")
        expected = ["作者称测试已通过，结果尚待独立验证。（来源：news.example.org · Hacker News）"]
        expected.extend(f"简讯{i}。（来源：阮一峰）" for i in range(2, 8))
        self.assertEqual(result.preview.splitlines()[2:-1],
                         [f"{i}. {line}" for i, line in enumerate(expected, 1)])
        self.assertEqual([line for line in result.markdown.splitlines() if "（来源：" in line], expected)

    def test_topics_and_numbers_match_full_document_after_deduplication(self):
        self.plan["items"][1]["topic"] = "网络安全"
        self.plan["items"][2]["topic"] = "开发者观察"  # 同一事件中评分较低的报道。
        result = edition.render(self.stories, self.plan, "2026-11-24")
        self.assertEqual([group["name"] for group in result.topics], ["网络安全", "公司动态"])
        rows = [row for group in result.topics for row in group["events"]]
        self.assertEqual([(r["number"], r["focus"]) for r in rows],
                         [(1, self.titles[1]), (2, self.titles[0]), (3, self.titles[5])])
        headings = [line for line in result.markdown.splitlines() if line.startswith("##")]
        self.assertEqual(headings, ["## 网络安全", f"### 1. {self.stories[1].heading}",
                                    "## 公司动态", f"### 2. {self.stories[0].heading}",
                                    f"### 3. {self.stories[5].heading}"])

    def test_rejects_incomplete_or_overlapping_groups(self):
        for events in ([[1, 3, 4, 5], [2]], [[1, 3, 4, 5], [1, 6], [2]]):
            plan = deepcopy(self.plan)
            plan["events"] = events
            with self.assertRaises(ValueError):
                edition.render(self.stories, plan, "2026-11-24")

    def test_empty_edition_and_unreadable_heading(self):
        raw = "# Horizon 每日速递\n\n已分析 12 条内容，但没有达到重要性阈值的条目。"
        result = edition.prepare(raw, "2026-11-24")
        self.assertIn("没有达到重要性阈值", result.preview)
        self.assertEqual(result.markdown, raw)
        self.assertEqual(result.topics, [])
        changed = self.raw.replace(" ⭐️ 9/10", " 格式改变", 1)
        result = edition.prepare(changed, "2026-11-24")
        self.assertIn("原始 Markdown", result.preview)
        self.assertEqual(result.markdown, changed)
