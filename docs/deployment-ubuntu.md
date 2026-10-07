# Deploy the Flask backend on Ubuntu

This runs the existing Flask app with Gunicorn behind Caddy for HTTPS. It keeps the current SQLite database for dashboard accounts only. Pump and sensor readings stay in memory; there are no new data tables or automatic pump shutoff rules.

## Before starting

You need an Ubuntu VPS with a public IP and a domain pointed at it. Allow inbound TCP ports 22, 80, and 443 in the VPS firewall. Do not expose Gunicorn's port 8000 publicly.

## Install

Connect to the VPS over SSH:

```sh
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip caddy
sudo useradd --system --home /var/lib/agw --create-home --shell /usr/sbin/nologin agw
sudo mkdir -p /opt/agw
sudo chown "$USER":"$USER" /opt/agw
git clone --branch deployment/secure-vps https://github.com/ArshakKhoshnevis/AGW-smart-garden-watering.git /opt/agw/repo
python3 -m venv /opt/agw/venv
/opt/agw/venv/bin/pip install -r /opt/agw/repo/AGW_web/requirements.txt
sudo chown -R root:agw /opt/agw
sudo chmod -R g+rX /opt/agw
sudo chown agw:agw /var/lib/agw
```

## Configure secrets

Generate a stable Flask secret key:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Create `/etc/agw.env` with that value and the same ESP32 login credentials configured in the firmware's local `credentials.json`:

```dotenv
AGW_SECRET_KEY=paste-the-generated-random-value
AGW_DATABASE_URL=sqlite:////var/lib/agw/users.db
AGW_DEVICE_USERNAME=your-esp32-login
AGW_DEVICE_PASSWORD=your-esp32-password
AGW_SESSION_COOKIE_SECURE=true
AGW_LOG_TIMEZONE=Asia/Tehran
```

Protect the file:

```sh
sudo chown root:agw /etc/agw.env
sudo chmod 640 /etc/agw.env
```

Do not commit `/etc/agw.env`, the generated secret, or device credentials to GitHub.

## Create the account database and dashboard login

This initializes the existing SQLite user table, then prompts you to create a dashboard login:

```sh
sudo -u agw sh -c 'set -a; . /etc/agw.env; set +a; cd /opt/agw/repo/AGW_web; /opt/agw/venv/bin/flask --app main init-db'
sudo -u agw sh -c 'set -a; . /etc/agw.env; set +a; cd /opt/agw/repo/AGW_web; /opt/agw/venv/bin/flask --app main create-admin'
```

## Run with Gunicorn and systemd

Create `/etc/systemd/system/agw.service`:

```ini
[Unit]
Description=AGW Flask backend
After=network-online.target
Wants=network-online.target

[Service]
User=agw
Group=agw
WorkingDirectory=/opt/agw/repo/AGW_web
EnvironmentFile=/etc/agw.env
ExecStart=/opt/agw/venv/bin/gunicorn --workers 1 --bind 127.0.0.1:8000 --access-logfile - --error-logfile - main:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Use one worker because the original app keeps the current pump and sensor values in memory.

## Enable HTTPS

Set `/etc/caddy/Caddyfile` to your domain:

```caddyfile
your-domain.example {
    reverse_proxy 127.0.0.1:8000
}
```

Replace `your-domain.example` with the domain pointed at the VPS, then start both services:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now agw
sudo systemctl enable --now caddy
sudo systemctl reload caddy
```

Open `https://your-domain.example` and sign in. The health check is `https://your-domain.example/healthz`.

The ESP32 must use the same domain as its `SERVER_URL` and the same login credentials in its ignored `credentials.json`. The ESP32 currently sends sensor updates about every five seconds. The dashboard itself polls the backend every two seconds.

## See ESP32 updates in the web app

While signed in, the dashboard's **ESP32** box shows whether fresh data is arriving. Expand it to see update lines; each accepted sensor update also appears once in the browser developer console. The status becomes stale when no ESP32 update has arrived for 15 seconds.

A typical line looks like:

```text
[2026-10-07 14:30:05] ESP32 data received: soil=[42%, 38%, 0%, 0%], pumps=[P1=OFF, P2=ON, P3=OFF, P4=OFF]
```

The log shows pump states stored by the backend. Sensor values and current pump states reset if the backend process restarts, matching the original in-memory behavior. The existing SQLite file stores dashboard accounts only.
