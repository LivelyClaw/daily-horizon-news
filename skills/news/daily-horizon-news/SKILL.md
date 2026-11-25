---
name: daily-horizon-news
description: 部署、调整或排查 Horizon 中文新闻简报、systemd 定时生成和 Hermes 飞书投递。
version: 0.3.0
author: Ben Barclay (benbarclay), Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [News, Briefings, Horizon, systemd, Feishu, Webhook]
    related_skills: [product-price-monitor]
---

# Horizon 中文新闻日报

Horizon 从多个来源采集资讯、筛选并生成中文日报。systemd 定时生成，Hermes 将日报发到飞书。配置数据源和模型时读 [Horizon 专项笔记](references/horizon.md)，安装或调整定时任务时读 [定时配置](references/scheduling.md)。

## When to Use

- 用户要「每天定时给我发日报/简报/新闻摘要」。
- 已有部署要改信息源、时间、渠道、模型、语言。
- 排查简报未发送、抓取为空、AI 阶段报错。

不适用于：一次性问新闻（直接 web_search）；价格监控（用 product-price-monitor）。

## 铁律

- **沿用已确认设置**。涉及部署位置、AI 提供商、内容方向、投递渠道和推送时间时，先核对现有设置及用户要求。对需要用户决定的缺项一次确认。全程中文回复。
- **配置前读仓库文档**：配置字段以固定版本的 `docs/configuration.md` 和 `data/config.example.json` 为准。默认值见 references/horizon.md。
- **每步用项目自身机制验证**：配置须通过加载和流程初始化，信息源单独做免密钥抓取，定时任务用实际运行日志验证。

## Procedure

### 1. 检查现有部署

改配置前先查：`ls ~/Horizon` 是否存在、`systemctl --user list-timers` 有无简报 timer、`~/Horizon/.env` 里是否还是占位符。已部署时只改要改的部分，不要重装。

### 2. 安装

按 [daily-horizon-news 的安装说明](https://github.com/LivelyClaw/daily-horizon-news#在服务器上安装) 将核对过的 Horizon 版本装到 `~/Horizon`。执行 `uv sync --locked` 后，入口在 `.venv/bin/horizon`。

### 3. 配置

读 `docs/configuration.md` + `data/config.example.json`，写 `data/config.json` 和 `.env`（密钥模板）。改动后按 references/horizon.md 加载配置并初始化 Horizon，检查所有引用的 profile。`.env` 占位符规则见 Pitfalls。

### 4. 免密钥验证抓取

AI key 没到位时也能测大半条链路：走 MCP 服务器调 `hz_fetch_items`，核对各源条数和状态（配方见 references/horizon.md）。区分无更新和抓取失败，按返回的错误排查失败源。

### 5. 定时

按 references/scheduling.md 建 systemd 用户级 service + timer，验证 `list-timers` 的下次触发时间换算成北京时间正确、`Linger=yes`。

### 6. 冒烟测试定时单元

启动一次服务，查看 `journalctl --user -u horizon-daily.service`。有资讯但缺模型密钥时，检查日志是否已完成抓取并进入 AI 阶段。窗口内所有源都正常返回零条时，Horizon 会提前结束，本次只验证到抓取。

### 7. 交付与收尾

按用户选定的渠道完成以下配置。填好真实密钥后，手动启动生成服务，在目标会话核对收到的中文日报。

- **签名 Webhook 和公开日报目录**。保持 Horizon 的 `webhook.enabled: false`，由 Hermes cron 使用 Horizon 虚拟环境运行投递脚本。运行 `--configure-webhook`，按安装说明从 Hermes 的 `.env` 读取应用凭据以及 `DAILY_HORIZON_NEWS_WEBHOOK_URL`、`DAILY_HORIZON_NEWS_WEBHOOK_SECRET`。每期独立导入「Horizon 中文日报」文件夹，标题以日期和早晚报开头。先确认文件夹及单篇链接均公开只读，再从签名 Webhook 推送全部带来源的一句话摘要和两个入口。
- **旧版 Markdown 附件**。用户指定此渠道时，使用 `--configure feishu:会话ID` 配置 Hermes 应用机器人。

## Pitfalls

- **配置文件模板的占位值必须 ASCII**（用 `PASTE_ZHIPU_API_KEY_HERE` 风格）：占位文字会被塞进 `Authorization` 请求头，HTTP 头只接受 latin-1，非 ASCII 会让每次 AI 调用在发出前就 UnicodeEncodeError，且报错藏在逐条重试里很难定位。
- **从 execute_code 起子进程跑项目 venv 时传干净 env 字典**（只含 PATH/HOME/LANG）：内核会注入自己的 PYTHONPATH/VIRTUAL_ENV，子进程会 import 到错误解释器站包里的依赖，报莫名其妙的 ImportError/版本错。
- 接手已有配置时，按 [数据源默认值](references/horizon.md#配置坑表) 区分省略整个源与仅省略源内的 `enabled`。
- **`systemctl --user start` 对 oneshot 服务会阻塞到流程结束**（简报流程含抓取+AI，几分钟很正常）：冒烟测试要么加超时预期，要么查状态/日志代替阻塞等待。
- **数据中心 IP 会被 Reddit 屏蔽**（RSS/JSON 端点一律 404）：本机是 VPS/机房 IP 时别调试 Reddit，直接换等效 RSS 源。
- 抓取条数骤降先查时间窗口（`--hours`/`collection.time_window_hours`）再查源：GitHub release、周更博客在 12h 窗口为 0 条属正常，不是故障。
- **paused 状态的 cron 任务无法被显式 run**（`execution_skipped: Job is paused/disabled`）——「暂停 + 事件触发」的设计走不通。
- **Hermes cron 不接受永不触发的计划**：`0 3 31 2 *` 这类不存在的日期会让 resume 直接报 `failed to find next date`。要用脚本看门狗（`*/5 * * * *` 轮询待发队列）而不是扭曲计划时间。
- 配置推理模型（如 glm-5.3-flash）时 Horizon 不发任何 thinking/effort 参数，走 API 默认（开启思考）：80+ 条打分 + 富化单期 20–35 分钟、约 25 万 token 是正常水平，别当故障排查。
- 调阈值时对照实际分数。评分支持小数，Horizon 保留分数大于等于阈值的条目。例如 6.8 分的条目在阈值 6.5 时保留，调至 7.0 后过滤。

## Verification

- [ ] 配置加载成功，Horizon 初始化识别了数据源和摘要引用的全部 profile。
- [ ] 在 `data.fetch_report.sources` 核对预期启用的源。正常状态包括有内容的 `success` 和窗口内无更新的 `empty`。逐项处理 `partial_failure` 和 `failure`。
- [ ] timer 下次触发时间 = 用户选的北京时间，Linger=yes，Persistent=true。
- [ ] 已用服务日志核对抓取、模型处理或空窗口退出的实际结果。
- [ ] 目标群收到签名 Webhook 速览，未登录的浏览器能打开当期全文和归档文件夹；事件数与文末内容完整。
