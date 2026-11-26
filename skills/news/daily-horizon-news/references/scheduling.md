# systemd 用户级定时配方

适用：本机部署的定时简报/任何周期任务，无 sudo 时的标准路径。

## 单元文件

`~/.config/systemd/user/<name>.service`：

```ini
[Unit]
Description=Horizon 中文日报生成

[Service]
Type=oneshot
WorkingDirectory=%h/Horizon
ExecStart=%h/Horizon/.venv/bin/horizon --hours 12
TimeoutStartSec=3h
```

WorkingDirectory 必须是仓库根（.env 和相对路径都从它解析）。ExecStart 用 venv 内绝对路径，不依赖 shell/uv。

选择 Hermes 投递时，按 [安装说明的飞书投递步骤](https://github.com/LivelyClaw/daily-horizon-news#3-配置飞书投递) 安装 `horizon-daily.service.d/hermes.conf` 和它引用的脚本。用 `systemctl --user cat horizon-daily.service` 确认包含带 `--enqueue-only` 的 `ExecStartPost`。它在下一期覆盖同名文件前保存正文，Hermes 离线时也能执行。

`~/.config/systemd/user/<name>.timer`：

```ini
[Unit]
Description=Horizon 日报定时触发（北京时间 08:00 / 20:00）

[Timer]
OnCalendar=*-*-* 00,12:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

## 启用与验证

```bash
systemctl --user daemon-reload
systemctl --user enable --now horizon-daily.timer
systemctl --user list-timers horizon-daily.timer --no-pager   # 看 NEXT 换算北京时间
loginctl show-user $USER -p Linger                     # 必须是 yes
systemd-analyze calendar "*-*-* 00,12:00:00 UTC"       # 先验 OnCalendar 语法与下次触发
```

- **时区换算**：服务器多为 UTC。北京 08:00/20:00 = UTC 00:00/12:00。OnCalendar 支持尾部时区后缀（`UTC`），比心算本地时间可靠。
- **Persistent=true**：机器关机错过的触发，开机后自动补跑一次。
- **Linger=yes**：用户级单元在不登录时也能跑。已开启则无需处理；未开启时 `loginctl enable-linger $USER` 自助尝试，失败再让用户 sudo。

## 坑

- `systemctl --user start <oneshot>.service` **阻塞到流程结束**（简报全流程数分钟）：冒烟测试时要么接受阻塞并给足超时，要么直接查 `systemctl --user status` + `journalctl --user -u <unit>`。
- 有资讯时从日志核对抓取和模型处理阶段。所有源正常返回零条时，服务会在抓取后结束，此次运行只验证到抓取。
- 日报漏发时，先用 `systemctl --user show horizon-daily.service -p Result -p TimeoutStartUSec` 核对生成结果。若为 `timeout`，在服务日志中找最后完成的阶段，并检查当期中文原稿是否已写入。原稿缺失时，按原定的 12 小时时间窗口补生成，完成后再检查飞书投递回执。
