---
name: self-hosted-briefings
description: 部署、调整或排查 Horizon 中文新闻简报、systemd 定时生成和 Hermes 飞书投递。
version: 0.1.0
author: Ben Barclay (benbarclay), Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [News, Briefings, Horizon, systemd, Feishu, Webhook]
    related_skills: [product-price-monitor]
---

# Self-Hosted Briefings（自托管每日简报）

Horizon 从多个来源采集资讯、筛选并生成中文日报。systemd 定时生成，Hermes 将日报发到飞书。配置数据源和模型时读 [Horizon 专项笔记](references/horizon.md)，安装或调整定时任务时读 [定时配置](references/scheduling.md)。

## When to Use

- 用户要「每天定时给我发日报/简报/新闻摘要」。
- 已有部署要改信息源、时间、渠道、模型、语言。
- 排查简报未发送、抓取为空、AI 阶段报错。

不适用于：一次性问新闻（直接 web_search）；价格监控（用 product-price-monitor）。

## 铁律

- **沿用已确认设置**。涉及部署位置、AI 提供商、内容方向、投递渠道和推送时间时，先核对现有设置及用户要求。对需要用户决定的缺项一次确认。全程中文回复。
- **配置前读仓库文档**：schema 以仓库内 docs/configuration.md 和 config.example.json 为准，不凭记忆猜字段；本项目模型校验会拒绝缺字段和未知值，但默认值可能出乎意料（见 references/horizon.md 的坑表）。
- **每步用项目自身机制验证**：配置用项目 loader 验证；抓取阶段单独测（不需要 AI key）；定时单元冒烟测试。不要以「命令退出码 0」当作成功。

## Procedure

### 1. 检查现有部署

改配置前先查：`ls ~/Horizon` 是否存在、`systemctl --user list-timers` 有无简报 timer、`~/Horizon/.env` 里是否还是占位符。已部署时只改要改的部分，不要重装。

### 2. 安装

按 [daily-horizon-news 的安装说明](https://github.com/LivelyClaw/daily-horizon-news#在服务器上安装) 将核对过的 Horizon 版本装到 `~/Horizon`。执行 `uv sync --locked` 后，入口在 `.venv/bin/horizon`。

### 3. 配置

读 `docs/configuration.md` + `data/config.example.json`，写 `data/config.json` 和 `.env`（密钥模板）。改动后立即用项目 loader 验证（配方见 references/horizon.md）。`.env` 占位符规则见 Pitfalls。

### 4. 免密钥验证抓取

AI key 没到位时也能测大半条链路：走 MCP 服务器调 `hz_fetch_items`，核对各源条数和状态（配方见 references/horizon.md）。某源持续失败就换等效源，不要反复调它。

### 5. 定时

按 references/scheduling.md 建 systemd 用户级 service + timer，验证 `list-timers` 的下次触发时间换算成北京时间正确、`Linger=yes`。

### 6. 冒烟测试定时单元

key 未就绪时启动一次服务，**预期在 AI 阶段报错**——这验证的是路径、.env 加载、抓取链路。日志用 `journalctl --user -u <unit>`。

### 7. 交付与收尾

用户填好真实密钥后：手动跑一次完整流程，确认用户渠道里收到中文日报，才算完成。投递渠道二选一：

- **Horizon 自带 webhook**（飞书群自定义机器人）：webhook.enabled=true + .env 里填 HORIZON_WEBHOOK_URL，feishu collapsible 卡片体验最好。
- **Hermes cron 投递**。将 `webhook.enabled` 设为 `false`。以 `--no-agent --script horizon_deliver_zh.py` 注册每 5 分钟运行的任务。脚本只输出当天新增或更新的简报，由 Hermes 投递到所选会话。脚本记录已输出的版本，实际投递结果在 Hermes 任务记录中查看。

## Pitfalls

- **配置文件模板的占位值必须 ASCII**（用 `PASTE_ZHIPU_API_KEY_HERE` 风格）：占位文字会被塞进 `Authorization` 请求头，HTTP 头只接受 latin-1，非 ASCII 会让每次 AI 调用在发出前就 UnicodeEncodeError，且报错藏在逐条重试里很难定位。
- **从 execute_code 起子进程跑项目 venv 时传干净 env 字典**（只含 PATH/HOME/LANG）：内核会注入自己的 PYTHONPATH/VIRTUAL_ENV，子进程会 import 到错误解释器站包里的依赖，报莫名其妙的 ImportError/版本错。
- **未配置 ≠ 未启用**：本项目 telegram/twitter/openbb 缺省 enabled=true，twitter 需要 Apify token、openbb 需要额外依赖，漏写显式 false 则每次运行都报错。对应通用规则：接手任何 schema 驱动的配置，先核对「省略字段」的默认行为。
- **`systemctl --user start` 对 oneshot 服务会阻塞到流程结束**（简报流程含抓取+AI，几分钟很正常）：冒烟测试要么加超时预期，要么查状态/日志代替阻塞等待。
- **数据中心 IP 会被 Reddit 屏蔽**（RSS/JSON 端点一律 404）：本机是 VPS/机房 IP 时别调试 Reddit，直接换等效 RSS 源。
- 抓取条数骤降先查时间窗口（`--hours`/`collection.time_window_hours`）再查源：GitHub release、周更博客在 12h 窗口为 0 条属正常，不是故障。
- **paused 状态的 cron 任务无法被显式 run**（`execution_skipped: Job is paused/disabled`）——「暂停 + 事件触发」的设计走不通。
- **Hermes cron 不接受永不触发的计划**：`0 3 31 2 *` 这类不存在的日期会让 resume 直接报 `failed to find next date`。要用脚本看门狗（`*/5 * * * *` + 输出空则不发）而不是扭曲计划时间。
- 配置推理模型（如 glm-5.3-flash）时 Horizon 不发任何 thinking/effort 参数，走 API 默认（开启思考）：80+ 条打分 + 富化单期 20–35 分钟、约 25 万 token 是正常水平，别当故障排查。
- **调阈值先看 profile 评分细则的档位**：rubric 按整数带划分（7-8 高价值、5-6 有趣），模型实际只给整数分，落在档位之间的阈值（如 6.5）与上一档位边界（7.0）效果完全一样，是无效调整。有效阈值只能取档位边界：7.0 → 6.0 → 5.0。

## Verification

- [ ] config.json 通过项目 loader 验证（不是仅 JSON 合法）。
- [ ] 免密钥抓取测试各源状态 success，总数与源配置规模相符。
- [ ] timer 下次触发时间 = 用户选的北京时间，Linger=yes，Persistent=true。
- [ ] 定时单元冒烟测试走过的阶段符合预期（到 AI 阶段才停）。
- [ ] 完整跑通一次：用户渠道收到中文日报。
