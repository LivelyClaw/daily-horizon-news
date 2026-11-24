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
TimeoutStartSec=3600
```

WorkingDirectory 必须是仓库根（.env 和相对路径都从它解析）。ExecStart 用 venv 内绝对路径，不依赖 shell/uv。

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
systemctl --user enable --now <name>.timer
systemctl --user list-timers <name>.timer --no-pager   # 看 NEXT 换算北京时间
loginctl show-user $USER -p Linger                     # 必须是 yes
systemd-analyze calendar "*-*-* 00,12:00:00 UTC"       # 先验 OnCalendar 语法与下次触发
```

- **时区换算**：服务器多为 UTC。北京 08:00/20:00 = UTC 00:00/12:00。OnCalendar 支持尾部时区后缀（`UTC`），比心算本地时间可靠。
- **Persistent=true**：机器关机错过的触发，开机后自动补跑一次。
- **Linger=yes**：用户级单元在不登录时也能跑。已开启则无需处理；未开启时 `loginctl enable-linger $USER` 自助尝试，失败再让用户 sudo。

## 坑

- `systemctl --user start <oneshot>.service` **阻塞到流程结束**（简报全流程数分钟）：冒烟测试时要么接受阻塞并给足超时，要么直接查 `systemctl --user status` + `journalctl --user -u <unit>`。
- 冒烟测试的预期要写清楚：key 未就绪时流程应走到 AI 阶段才报错（如 401），抓取阶段正常——这是「单元配置正确」的证据。
