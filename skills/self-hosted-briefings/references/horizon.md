# Horizon 专项笔记

仓库：Thysrael/Horizon（MIT）。Python + uv；CLI 入口在 `.venv/bin/`：`horizon`、`horizon-webhook`（dry-run 测试推送）、`horizon-mcp`、`horizon-wizard`。配置：`data/config.json`（模型校验，启动即验）；密钥：仓库根 `.env`，从工作目录加载——所以 service 的 WorkingDirectory 必须是仓库根。

## 中文日报示例配置

- 智谱示例使用 `provider: openai`、`base_url: https://open.bigmodel.cn/api/paas/v4`、`api_key_env: ZHIPU_API_KEY` 和模型 `glm-5.3-flash`。
- 中文输出：`ai.languages: ["zh"]`。
- 定时：北京 08:00 / 20:00 两期，`--hours 12` 窗口，`digest.max_items: 15`。
- Hermes 飞书投递使用 `webhook.enabled: false`。同目录安装 `horizon_deliver_zh.py` 和 `horizon_edition.py`，分别负责投递和编辑。原生折叠卡片配置见下方「飞书机器人」。
- 定时器：systemd 用户级 `horizon-daily.timer`。

## 配置坑表

| 坑 | 规则 |
|---|---|
| 数据源的默认值 | 省略整个 `twitter` 或 `openbb` 段时，相应源关闭。提供这两个段时，段内 `enabled` 默认为 true。省略整个 `telegram` 段时，默认启用且频道列表为空，可显式关闭。 |
| email 段即使 `enabled: false` 也必填 smtp_server/imap_server/email_address | 照抄 example 里的占位值即可 |
| webhook URL 必须 http(s) | `HORIZON_WEBHOOK_URL` 是占位文字时启动即 `HZ_INTERNAL_ERROR`；测试期先 `webhook.enabled: false` |
| `ai.languages` 缺省 ["en"] | 中文日报必须显式 `"zh"` |
| 未知 profile ID / 缺字段 | 配置加载器检查字段，Horizon 初始化时检查 profile 是否存在 |

## 验证配方

在 `~/Horizon` 下执行。这里按 CLI 的顺序加载 `.env`、配置文件和 profile，完成初始化即可发现错误的引用。此步骤无需抓取资讯或调用模型。

```bash
.venv/bin/python - <<'PYTHON'
from dotenv import load_dotenv
from src.orchestrator import HorizonOrchestrator
from src.storage.manager import StorageManager

load_dotenv(".env")
storage = StorageManager()
config = storage.load_config()
app = HorizonOrchestrator(config, storage)
if app.webhook_notifier and not app.webhook_notifier.url:
    raise SystemExit("webhook 已启用，请检查 url_env 和 .env 中对应的 URL")
print("配置和 profile 校验通过")
PYTHON
```

**免密钥抓取测试**：起 `.venv/bin/horizon-mcp` 子进程，stdio JSON-RPC：`initialize` → `notifications/initialized` → `tools/call hz_fetch_items {"hours": 12}`。读取工具结果中的应用层 JSON，先检查 `ok`。成功时 `data.fetch_report.sources[]` 列出各源状态、条数及错误。请求成功、有内容为 `success`，窗口内没有更新为 `empty`。出现 `partial_failure` 或 `failure` 时检查该源的 `failures` 或 `error`。子进程务必传干净 env（见 SKILL.md Pitfalls），工作目录设仓库根。

**完整流程**：`.venv/bin/horizon --hours 12`（或 `-d`/`-c` 指路径），产物在 `data/summaries/`。

## 信息源排查记录

- **Reddit 全端点 404**（机房 IP 被封）→ 用 RSS 等效源替代，勿调试。
- 已验证可用 feed：IT之家 `https://www.ithome.com/rss/`（量大，撑中文科技面）、爱范儿 `https://www.ifanr.com/feed`、阮一峰 `https://www.ruanyifeng.com/blog/atom.xml`、少数派 `https://sspai.com/feed`、TechCrunch `https://techcrunch.com/feed/`、Ars Technica `https://feeds.arstechnica.com/arstechnica/index`、GitHub Blog `https://github.blog/feed/`、Simon Willison `https://simonwillison.net/atom/everything/`。
- **36氪 `https://36kr.com/feed` 返回 HTML**，feedparser 报 Invalid feed，不可用。
- GitHub repo_releases 12h 窗口 0 条属正常（看 release 频率）；OSS Insight 若持续 0 条，状态是 empty 不是 failure，无害可留。

## 飞书机器人

使用 Horizon 原生飞书群机器人时，将 `data/config.json` 中的 `webhook` 段设为以下内容，并在 Horizon 根目录的 `.env` 中设置 `HORIZON_WEBHOOK_URL` 为真实机器人地址。

```json
{
  "enabled": true,
  "url_env": "HORIZON_WEBHOOK_URL",
  "platform": "feishu",
  "layout": "collapsible",
  "languages": ["zh"]
}
```

- 安全设置选**自定义关键词**，填 `Horizon`——折叠卡片标题是 `Horizon {date} 折叠日报`，天然含该词。签名校验 Horizon 不支持；IP 白名单对无固定 IP 的本机不可用。
- 测试推送用 `horizon-webhook --dry-run` 预览。
- CI 备选：仓库自带 `.github/workflows/daily-summary.yml.disabled` 模板（改 secrets + rename 启用），本机不常开时用它。
