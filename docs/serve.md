# 常駐させる・ほかの端末から聴く

## systemd のユーザーサービスにする

`~/.config/systemd/user/piano-improv.service`（パスは自分の clone の場所に）:

```ini
[Unit]
Description=piano-improv
After=network.target

[Service]
WorkingDirectory=%h/piano-improv
ExecStart=%h/piano-improv/.venv/bin/piano serve --no-speaker --port 8765
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now piano-improv
journalctl --user-unit piano-improv -f      # ログ
loginctl enable-linger "$USER"              # ログアウトしても動かし続けるなら（sudo が要る環境もある）
```

`--no-speaker` は、スピーカーで鳴らさず配信だけにします。部屋で鳴らしたいときは外してください。
手で `piano serve` を動かすときは、ポートが重なるので先に `systemctl --user stop piano-improv`。

## ほかの端末から開く

認証がないので、インターネットにそのまま出さないでください。

- **同じ LAN の中だけ**：`piano serve --host 0.0.0.0`（同じネットワークの誰でも操作できます）
- **Tailscale**（自分の端末だけ・https）：`127.0.0.1` のまま起動して、

  ```bash
  sudo tailscale serve --bg --https=8765 http://127.0.0.1:8765
  ```

  `https://<マシン名>.<tailnet>.ts.net:8765/` で開けます。Funnel（インターネット公開）は使わないでください
