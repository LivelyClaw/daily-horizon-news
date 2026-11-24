# daily-horizon-news

用 Horizon 生成中文科技新闻简报，通过 Hermes 发到飞书。systemd 默认在北京时间 **08:00 和 20:00** 各生成一期，每次取最近 12 小时的资讯。Hermes 每 5 分钟检查当天的中文日报，发现新版本就推送。

## 仓库内容

| 位置 | 用途 |
| --- | --- |
| [skills/self-hosted-briefings/](skills/self-hosted-briefings/) | Hermes skill 和部署、排查笔记 |
| [scripts/horizon_deliver_zh.py](scripts/horizon_deliver_zh.py) | 读取当天的中文日报，清理飞书纯文本中无法展开的内容，并记录已输出的版本 |
| [deploy/systemd/](deploy/systemd/) | 生成日报的用户级 service 和 timer |
| [config/horizon.example.json](config/horizon.example.json) | 中文输出、公开科技资讯源和模型的示例配置 |

Horizon 主程序使用已核对的上游版本。[Thysrael/Horizon · `e17ea80`](https://github.com/Thysrael/Horizon/tree/e17ea80ced84e575e89489f3905b75eabf7e249b)

## 在服务器上安装

在运行 Hermes 的 Ubuntu 用户下执行，准备好 Git、Python 3.11 或更新版本、`uv` 和已启用的 Hermes 飞书通道。已有 Horizon 部署时，复用现有的 `~/Horizon`、模型配置和同名定时任务。

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

install -Dm644 deploy/systemd/horizon-daily.service "$HOME/.config/systemd/user/horizon-daily.service"
install -Dm644 deploy/systemd/horizon-daily.timer "$HOME/.config/systemd/user/horizon-daily.timer"
systemctl --user daemon-reload
systemctl --user enable --now horizon-daily.timer
```

需要在退出登录后继续执行时，检查 `loginctl show-user "$USER" -p Linger`。如未启用，可用 `loginctl enable-linger "$USER"` 开启。

### 3. 配置飞书投递

先用 `hermes cron list` 查看已有任务。首次设置时，将示例里的 `feishu:oc_REPLACE` 换成机器人能发送消息的飞书会话，再注册定时任务。[Hermes 定时任务说明](https://hermes-agent.nousresearch.com/docs/user-guide/features/cron)

```bash
hermes cron create '*/5 * * * *' \
  --name 'Horizon 中文日报投递' \
  --script horizon_deliver_zh.py --no-agent --no-wrap-response \
  --timezone Asia/Shanghai --deliver 'feishu:oc_REPLACE'
```

## 查看运行状态

- 用 `systemctl --user list-timers horizon-daily.timer` 查看下一次生成时间，用 `journalctl --user -u horizon-daily.service -n 50` 查看生成日志。
- 完整日报在 `~/Horizon/data/summaries/horizon-YYYY-MM-DD-zh.md`。同一天的第二期更新同一个文件，投递脚本按文件修改时间识别新版本。
- 用 `hermes cron list` 找到「Horizon 中文日报投递」，在 Hermes 运行记录中查看投递结果。Horizon 的配置、日报和脚本状态保存在服务器本地。
