# Horizon 专项笔记

仓库：Thysrael/Horizon（MIT）。Python + uv；CLI 入口在 `.venv/bin/`：`horizon`、`horizon-webhook`（dry-run 测试推送）、`horizon-mcp`、`horizon-wizard`。配置：`data/config.json`（模型校验，启动即验）；密钥：仓库根 `.env`，从工作目录加载——所以 service 的 WorkingDirectory 必须是仓库根。

## 中文日报示例配置

- 智谱示例使用 `provider: openai`、`base_url: https://open.bigmodel.cn/api/paas/v4`、`api_key_env: ZHIPU_API_KEY` 和模型 `glm-5.3-flash`。
- 中文输出：`ai.languages: ["zh"]`。
- 定时：北京 08:00 / 20:00 两期，`--hours 12` 窗口，`digest.max_items: 15`。
- Hermes 飞书投递使用 `webhook.enabled: false` 和 `horizon_deliver_zh.py`。如果使用 Horizon 自带的飞书折叠卡片，可配置 `platform: feishu`、`layout: collapsible`、`languages: ["zh"]` 和 `url_env: HORIZON_WEBHOOK_URL`。
- 定时器：systemd 用户级 `horizon-daily.timer`。

## 配置坑表

| 坑 | 规则 |
|---|---|
| telegram/twitter/openbb 缺省 enabled=true | 不用的源显式写 `"enabled": false`，否则 twitter 缺 APIFY_TOKEN、openbb 缺 extra 每次运行报错 |
| email 段即使 `enabled: false` 也必填 smtp_server/imap_server/email_address | 照抄 example 里的占位值即可 |
| webhook URL 必须 http(s) | `HORIZON_WEBHOOK_URL` 是占位文字时启动即 `HZ_INTERNAL_ERROR`；测试期先 `webhook.enabled: false` |
| `ai.languages` 缺省 ["en"] | 中文日报必须显式 `"zh"` |
| 未知 profile ID / 缺字段 | 启动时 pydantic 校验直接拒绝，改完跑 loader 验证 |

## 验证配方

在 `~/Horizon` 目录下，用 Horizon 自带的 Python 验证配置。

```bash
.venv/bin/python -c 'from pathlib import Path; from src.mcp.horizon_adapter import load_config; load_config(Path("data/config.json"))'
```

**免密钥抓取测试**：起 `.venv/bin/horizon-mcp` 子进程，stdio JSON-RPC：`initialize` → `notifications/initialized` → `tools/call hz_fetch_items {"hours": 12}`。返回的 `meta.fetch_report.sources[]` 给出每源状态/条数/错误。子进程务必传干净 env（见 SKILL.md Pitfalls），工作目录设仓库根。

**完整流程**：`.venv/bin/horizon --hours 12`（或 `-d`/`-c` 指路径），产物在 `data/summaries/`。

## 信息源排查记录

- **Reddit 全端点 404**（机房 IP 被封）→ 用 RSS 等效源替代，勿调试。
- 已验证可用 feed：IT之家 `https://www.ithome.com/rss/`（量大，撑中文科技面）、爱范儿 `https://www.ifanr.com/feed`、阮一峰 `https://www.ruanyifeng.com/blog/atom.xml`、少数派 `https://sspai.com/feed`、TechCrunch `https://techcrunch.com/feed/`、Ars Technica `https://feeds.arstechnica.com/arstechnica/index`、GitHub Blog `https://github.blog/feed/`、Simon Willison `https://simonwillison.net/atom/everything/`。
- **36氪 `https://36kr.com/feed` 返回 HTML**，feedparser 报 Invalid feed，不可用。
- GitHub repo_releases 12h 窗口 0 条属正常（看 release 频率）；OSS Insight 若持续 0 条，状态是 empty 不是 failure，无害可留。

## 飞书机器人

- 安全设置选**自定义关键词**，填 `Horizon`——折叠卡片标题是 `Horizon {date} 折叠日报`，天然含该词。签名校验 Horizon 不支持；IP 白名单对无固定 IP 的本机不可用。
- 测试推送用 `horizon-webhook --dry-run` 预览。
- CI 备选：仓库自带 `.github/workflows/daily-summary.yml.disabled` 模板（改 secrets + rename 启用），本机不常开时用它。
