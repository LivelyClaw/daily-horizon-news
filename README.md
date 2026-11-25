# daily-horizon-news

用 Horizon 生成中文科技新闻简报，通过 Hermes 发到飞书。systemd 默认在北京时间 **08:00 和 20:00** 各生成一期，每次取最近 12 小时的资讯。每期生成后立即保存完整原稿。Hermes 每 5 分钟处理最早的一期待发日报，将本期 Markdown 导入飞书归档文件夹，再用签名 Webhook 发送带来源的速览、本期全文和历期目录入口。

## 仓库内容

| 位置 | 用途 |
| --- | --- |
| [skills/news/daily-horizon-news/](skills/news/daily-horizon-news/) | 新闻分类下的 Hermes 技能和部署、排查笔记 |
| [scripts/horizon_deliver_zh.py](scripts/horizon_deliver_zh.py) | 保存中文日报版本、通过 Hermes 发送到飞书，并记录发送结果 |
| [scripts/horizon_edition.py](scripts/horizon_edition.py) | 归并同一事件、挑选代表报道，生成速览和完整 Markdown |
| [deploy/systemd/](deploy/systemd/) | 生成日报的用户级 service 和 timer |
| [config/horizon.example.json](config/horizon.example.json) | 中文输出、公开科技资讯源和模型的示例配置 |

Horizon 主程序使用已核对的上游版本。[Thysrael/Horizon · `098375a`](https://github.com/Thysrael/Horizon/tree/098375a1f19ca26d3603df1e668bd97b34950763)

## 群里会收到什么

- **速览**按主题展开本期去重后的全部事件，组内按评分排列。速览和全文共用一套连续编号。每条用一句中文概括，重点加粗，句末以灰字标注来源。RSS 优先显示订阅源名称，经 Hacker News 等渠道收录的报道显示原文域名和渠道。
- **本期全文**在「Horizon 中文日报」文件夹中，每期一篇，命名如 `2026-11-25 早报｜Horizon 中文日报`。文档和文件夹均对持链接者开放阅读。速览末尾可打开本期或浏览历期。编辑后的 Markdown 同时保存在 `~/Horizon/data/editions/`。
- 编辑时复用 Horizon 已配置的模型，每个版本通常增加一次调用，用标题、来源和报道开头判断是否是同一件事以及直接后续。同组优先保留原评分最高的报道。同分时依次看来源的证据质量、原始发布者的传播影响力。缺少统一阅读量时按模型可辨识的来源层级估计，仍相同则取原顺序靠前者。同一人物参与的其他独立事件可以同时保留。

## 在服务器上安装

在运行 Hermes 的 Ubuntu 用户下执行。已有日报、只需迁移技能目录时，直接执行第 2 步的第一段命令即可。

部署整套日报需要 Git、Python 3.11.5 或更新版本、`uv` 、Hermes 定时任务和飞书自建应用。已有部署时，保留现有的 `~/Horizon` 和模型配置。升级日报程序前用 `hermes cron list` 找到原投递任务，记下任务 ID 和飞书会话，用 `hermes cron pause 任务ID` 暂停它。第 3 步会更新并恢复这个任务。

### 1. 准备 Horizon

以下命令适用于首次安装。

```bash
git clone https://github.com/LivelyClaw/daily-horizon-news.git
cd daily-horizon-news

git clone https://github.com/Thysrael/Horizon.git "$HOME/Horizon"
git -C "$HOME/Horizon" checkout --detach 098375a1f19ca26d3603df1e668bd97b34950763
(cd "$HOME/Horizon" && uv sync --locked)
install -m 600 config/horizon.example.json "$HOME/Horizon/data/config.json"
```

示例采用智谱的 OpenAI 兼容接口。在服务器的 `~/Horizon/.env` 中设置 `ZHIPU_API_KEY` 并将文件权限设为 `600`。使用其他模型时，按对应服务调整示例里的 `ai` 配置。使用 Hermes 投递时，确保 `ai.languages` 含 `zh`，并将 `webhook.enabled` 设为 `false`。[Horizon 配置参考](https://github.com/Thysrael/Horizon/blob/098375a1f19ca26d3603df1e668bd97b34950763/docs/configuration.md)

日报编辑默认沿用 Horizon 的 `ai.max_tokens`。使用示例中的 `glm-5.3-flash` 时，可在 `~/Horizon/.env` 中单独设置 `HORIZON_EDITION_MAX_TOKENS=8192`，给事件归并和一句话摘要留出足够的输出空间。

### 2. 安装 skill 和生成定时器

在本仓库根目录执行。技能默认安装到 `~/.hermes/skills/news/daily-horizon-news/`。如果以前装在 `productivity` 分类，下面的命令会在复制成功后将旧技能移出技能目录，备份中保留原有文件。

```bash
horizon_hermes_home="${HERMES_HOME:-$HOME/.hermes}"
old_skill="$horizon_hermes_home/skills/productivity/self-hosted-briefings"
mkdir -p "$horizon_hermes_home/skills/news" &&
  cp -R skills/news/daily-horizon-news "$horizon_hermes_home/skills/news/" &&
  if [ -e "$old_skill" ] || [ -L "$old_skill" ]; then
    backup_dir=$(mktemp -d "$horizon_hermes_home/skill-backup.XXXXXX") &&
      mv "$old_skill" "$backup_dir/" &&
      echo "旧技能已备份到 $backup_dir/"
  fi
```

安装或升级日报程序时，继续安装投递脚本和定时器。

```bash
mkdir -p "$horizon_hermes_home/scripts"
install -m 755 scripts/horizon_deliver_zh.py "$horizon_hermes_home/scripts/"
install -m 644 scripts/horizon_edition.py scripts/horizon_docs.py scripts/horizon_webhook.py "$horizon_hermes_home/scripts/"

install -Dm644 deploy/systemd/horizon-daily.service "$HOME/.config/systemd/user/horizon-daily.service"
install -Dm644 deploy/systemd/horizon-daily.timer "$HOME/.config/systemd/user/horizon-daily.timer"
```

需要在退出登录后继续执行时，检查 `loginctl show-user "$USER" -p Linger`。如未启用，可用 `loginctl enable-linger "$USER"` 开启。

### 3. 配置飞书投递

在 `~/.hermes/.env`（此部署为 `/home/lazyboots/.hermes/.env`）中设置以下四项。脚本每次投递直接读取此文件。

- `FEISHU_APP_ID`、`FEISHU_APP_SECRET` 用于创建文件夹、导入 Markdown 及设置文档分享权限。应用需要对应的云空间读写权限。
- `DAILY_HORIZON_NEWS_WEBHOOK_URL`、`DAILY_HORIZON_NEWS_WEBHOOK_SECRET` 对应目标群自定义机器人的地址和签名密钥。在机器人安全设置中开启「签名校验」。[飞书签名说明](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot?lang=zh-CN#3c6592d6)

首次配置会在应用云空间创建专用文件夹。由有管理权限的账号在该文件夹的「分享」面板选择「互联网获得链接的人」和「可阅读」，再运行配置命令。应用拥有的新文件夹可通过飞书协作者接口添加指定的文件夹管理员。程序回读文件夹权限后启用新渠道，每篇文档也会单独核对权限。

```bash
"$HOME/Horizon/.venv/bin/python" "$horizon_hermes_home/scripts/horizon_deliver_zh.py" --configure-webhook
mkdir -p "$HOME/.local/libexec"
ln -sfn "$horizon_hermes_home/scripts/horizon_deliver_zh.py" "$HOME/.local/libexec/horizon_deliver_zh.py"
install -Dm644 deploy/systemd/horizon-daily.service.d/hermes.conf "$HOME/.config/systemd/user/horizon-daily.service.d/hermes.conf"
systemctl --user daemon-reload
systemctl --user enable --now horizon-daily.timer
```

systemd 在每次成功生成后运行一次 `--enqueue-only`，把正文保存到本地队列。即使 Hermes 暂停一整天，早晚两期也可以分别补发。旧版的 `.delivery_state_zh.json` 只记录过脚本输出，无法证明飞书已收到。升级时若当前 UTC 日期已有日报，会重新发送一次，更早的历史日报保留在原目录。从已有 SQLite 队列升级时，已确认的版本保持已确认。旧待发版本优先按内容摘要恢复完整原稿。原稿已被覆盖时，保留旧快照，已截断的版本明确标为节选。

首次安装时创建下列任务。脚本直接检查 Webhook 回执，cron 的结果目标填 `local`。[Hermes 定时任务](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)

```bash
hermes cron create '*/5 * * * *' \
  --name 'Horizon 中文日报投递' \
  --script horizon_deliver_zh.py --interpreter "$HOME/Horizon/.venv/bin/python" --no-agent --no-wrap-response \
  --timezone Asia/Shanghai --deliver local
```

升级已有任务时，用以下两条命令替代上面的创建命令，把 `任务ID` 换成原任务的 ID。

```bash
hermes cron edit 任务ID --interpreter "$HOME/Horizon/.venv/bin/python" --deliver local
hermes cron resume 任务ID
```

切换群机器人时，更新 Hermes `.env` 中的 Webhook 地址和签名密钥。下次投递直接使用新值。旧版应用机器人附件模式仍可通过 `--configure feishu:会话ID` 选择。

## 查看运行状态

- 用 `systemctl --user list-timers horizon-daily.timer` 查看下一次生成时间，用 `journalctl --user -u horizon-daily.service -n 50` 查看生成及保存待发正文的结果。
- Horizon 的原始日报在 `~/Horizon/data/summaries/horizon-YYYY-MM-DD-zh.md`，文件日期使用 UTC。同一天的第二期会更新同一个文件。手动连续生成多期时，每次生成结束后也要执行一次 `"$horizon_hermes_home/scripts/horizon_deliver_zh.py" --enqueue-only`，保留即将被覆盖的版本。
- 用 `hermes cron runs 任务ID` 查看执行记录。SQLite 会保存导入任务、文档链接和已确认的卡片页数。Webhook 失败后复用该篇文档，从未确认页续发。若飞书已收件但回执丢失，该页重试可能重复送达。
- 用以下查询查看最近五期的状态及云文档链接。

```bash
sqlite3 -readonly -header "$HOME/Horizon/data/.delivery_zh.sqlite3" \
  "SELECT e.day, e.edition_name, e.phase, p.url, p.next_page FROM editions e
   LEFT JOIN publications p ON p.edition=e.rowid ORDER BY e.rowid DESC LIMIT 5"
```
