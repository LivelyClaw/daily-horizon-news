# daily-horizon-news

用 Horizon 生成中文科技新闻简报，通过 Hermes 发到飞书。systemd 默认在北京时间 **08:00 和 20:00** 各生成一期，每次取最近 12 小时的资讯。每期生成后立即保存完整原稿。Hermes 每 5 分钟处理最早的一期待发日报，先发带来源的简短速览，再发可直接转发的 Markdown 文件。

## 仓库内容

| 位置 | 用途 |
| --- | --- |
| [skills/self-hosted-briefings/](skills/self-hosted-briefings/) | Hermes skill 和部署、排查笔记 |
| [scripts/horizon_deliver_zh.py](scripts/horizon_deliver_zh.py) | 保存中文日报版本、通过 Hermes 发送到飞书，并记录发送结果 |
| [scripts/horizon_edition.py](scripts/horizon_edition.py) | 归并同一事件、挑选代表报道，生成速览和完整 Markdown |
| [deploy/systemd/](deploy/systemd/) | 生成日报的用户级 service 和 timer |
| [config/horizon.example.json](config/horizon.example.json) | 中文输出、公开科技资讯源和模型的示例配置 |

Horizon 主程序使用已核对的上游版本。[Thysrael/Horizon · `e17ea80`](https://github.com/Thysrael/Horizon/tree/e17ea80ced84e575e89489f3905b75eabf7e249b)

## 群里会收到什么

- **速览**按附件顺序列出本期去重后的全部事件，每条用一句中文概括，句末标注代表报道的来源。RSS 优先显示订阅源名称，经 Hacker News 等渠道收录的报道显示原文域名和渠道。
- **Markdown 附件**收录本期所有入选事件，每个事件只保留一篇代表报道的完整详情、原文链接和参考资料。早晚两期使用不同的版本文件名，可以直接在飞书里转发。
- 编辑时复用 Horizon 已配置的模型，每个版本通常增加一次调用，用标题、来源和报道开头判断是否是同一件事以及直接后续。同组优先保留原评分最高的报道。同分时依次看来源的证据质量、原始发布者的传播影响力。缺少统一阅读量时按模型可辨识的来源层级估计，仍相同则取原顺序靠前者。同一人物参与的其他独立事件可以同时保留。

## 在服务器上安装

在运行 Hermes 的 Ubuntu 用户下执行，准备好 Git、Python 3.11 或更新版本、`uv` 和已启用的 Hermes 飞书通道。已有部署时，保留现有的 `~/Horizon` 和模型配置。升级前用 `hermes cron list` 找到原投递任务，记下任务 ID 和飞书会话，用 `hermes cron pause 任务ID` 暂停它。第 3 步会更新并恢复这个任务。

### 1. 准备 Horizon

以下命令适用于首次安装。

```bash
git clone https://github.com/LivelyClaw/daily-horizon-news.git
cd daily-horizon-news

git clone https://github.com/Thysrael/Horizon.git "$HOME/Horizon"
git -C "$HOME/Horizon" checkout --detach e17ea80ced84e575e89489f3905b75eabf7e249b
(cd "$HOME/Horizon" && uv sync --locked)
install -m 600 config/horizon.example.json "$HOME/Horizon/data/config.json"
```

示例采用智谱的 OpenAI 兼容接口。在服务器的 `~/Horizon/.env` 中设置 `ZHIPU_API_KEY` 并将文件权限设为 `600`。使用其他模型时，按对应服务调整示例里的 `ai` 配置。使用 Hermes 投递时，确保 `ai.languages` 含 `zh`，并将 `webhook.enabled` 设为 `false`。[Horizon 配置参考](https://github.com/Thysrael/Horizon/blob/e17ea80ced84e575e89489f3905b75eabf7e249b/docs/configuration.md)

### 2. 安装 skill 和生成定时器

在本仓库根目录执行。

```bash
horizon_hermes_home="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$horizon_hermes_home/skills/productivity" "$horizon_hermes_home/scripts"
cp -R skills/self-hosted-briefings "$horizon_hermes_home/skills/productivity/"
install -m 755 scripts/horizon_deliver_zh.py "$horizon_hermes_home/scripts/"
install -m 644 scripts/horizon_edition.py "$horizon_hermes_home/scripts/"

install -Dm644 deploy/systemd/horizon-daily.service "$HOME/.config/systemd/user/horizon-daily.service"
install -Dm644 deploy/systemd/horizon-daily.timer "$HOME/.config/systemd/user/horizon-daily.timer"
```

需要在退出登录后继续执行时，检查 `loginctl show-user "$USER" -p Linger`。如未启用，可用 `loginctl enable-linger "$USER"` 开启。

### 3. 配置飞书投递

在同一终端执行。先用 `hermes send --help` 确认当前 Hermes 支持 `--to`、`--file` 和 `--json`。将 `feishu:oc_REPLACE` 换成已确认的飞书会话。配置步骤会保存当前 Hermes 的可执行文件路径、配置目录和目标会话，首次从当前 UTC 日期开始收集。

```bash
"$horizon_hermes_home/scripts/horizon_deliver_zh.py" --configure 'feishu:oc_REPLACE'
mkdir -p "$HOME/.local/libexec"
ln -sfn "$horizon_hermes_home/scripts/horizon_deliver_zh.py" "$HOME/.local/libexec/horizon_deliver_zh.py"
install -Dm644 deploy/systemd/horizon-daily.service.d/hermes.conf "$HOME/.config/systemd/user/horizon-daily.service.d/hermes.conf"
systemctl --user daemon-reload
systemctl --user enable --now horizon-daily.timer
```

systemd 在每次成功生成后运行一次 `--enqueue-only`，把正文保存到本地队列。即使 Hermes 暂停一整天，早晚两期也可以分别补发。旧版的 `.delivery_state_zh.json` 只记录过脚本输出，无法证明飞书已收到。升级时若当前 UTC 日期已有日报，会重新发送一次，更早的历史日报保留在原目录。从已有 SQLite 队列升级时，已确认的版本保持已确认。旧待发版本优先按内容摘要恢复完整原稿。原稿已被覆盖时，保留旧快照，已截断的版本明确标为节选。

首次安装时创建下列任务。脚本自行调用 `hermes send` 获取回执，因此 cron 的结果目标填 `local`。[Hermes 发送命令](https://hermes-agent.nousresearch.com/docs/reference/cli-commands#hermes-send) · [Hermes 定时任务](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)

```bash
hermes cron create '*/5 * * * *' \
  --name 'Horizon 中文日报投递' \
  --script horizon_deliver_zh.py --no-agent --no-wrap-response \
  --timezone Asia/Shanghai --deliver local
```

升级已有任务时，用以下两条命令替代上面的创建命令，把 `任务ID` 换成原任务的 ID。

```bash
hermes cron edit 任务ID --deliver local
hermes cron resume 任务ID
```

切换飞书会话时，重新执行 `--configure`。下一次投递会将尚待发送的一期送到新目标。如果旧会话只收到过速览，新会话会收到速览和附件。若改用 Horizon 原生 webhook，请暂停 Hermes 投递任务、移除上面安装的 `hermes.conf`、执行 `systemctl --user daemon-reload`，再按技能笔记配置群机器人。[飞书 webhook 配置](skills/self-hosted-briefings/references/horizon.md#飞书机器人)

## 查看运行状态

- 用 `systemctl --user list-timers horizon-daily.timer` 查看下一次生成时间，用 `journalctl --user -u horizon-daily.service -n 50` 查看生成及保存待发正文的结果。
- Horizon 的原始日报在 `~/Horizon/data/summaries/horizon-YYYY-MM-DD-zh.md`，文件日期使用 UTC。同一天的第二期会更新同一个文件。手动连续生成多期时，每次生成结束后也要执行一次 `"$horizon_hermes_home/scripts/horizon_deliver_zh.py" --enqueue-only`，保留即将被覆盖的版本。
- 用 `hermes cron runs 任务ID` 查看执行记录。编辑失败时保留原稿重试。速览和附件分别确认，向同一会话补发附件会跳过已经成功的速览。失败或回执无法确认时，待发版本留在 `~/Horizon/data/.delivery_zh.sqlite3`，下次轮询重试。若飞书已收件但回执丢失，重试可能重复送达。最终验收以目标会话收到对应日报为准。
- 如服务器上有 SQLite 命令行，可查看最近五个版本的状态和飞书消息 ID。

```bash
sqlite3 -readonly -header "$HOME/Horizon/data/.delivery_zh.sqlite3" \
  "SELECT day, substr(hex(digest), 1, 12) AS version,
          CASE WHEN body IS NULL THEN '已确认' WHEN phase = 'file' THEN '待发附件' ELSE '待编辑或发速览' END AS status, message_id
   FROM editions ORDER BY rowid DESC LIMIT 5"
```
