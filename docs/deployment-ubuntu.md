# Deploy AGW on an Ubuntu VPS

This guide runs the Flask dashboard on a small Ubuntu VPS with Gunicorn behind Caddy. Caddy obtains and renews HTTPS certificates automatically. The ESP32 makes outbound HTTPS requests, so no inbound connection to your home network is needed.

You need a domain name pointed at the VPS before the TLS certificate and ESP32 connection can work. Do not put real passwords, device tokens, or private keys in GitHub.

## 1. Prepare the VPS and domain

Create an Ubuntu 24.04 LTS VPS with at least 1 vCPU, 1–2 GB RAM, and 20 GB disk. Give it a public IPv4 address. Add DNS A records for your domain (and optionally `www`) pointing to that IP. Wait until DNS resolves before configuring Caddy.

Allow inbound TCP ports 22, 80, and 443 in the provider firewall. Keep SSH restricted to your own IP if the provider supports it. Do not expose port 8000.

Connect over SSH, update packages, and install the server packages:

```sh
sudo apt update
sudo apt upgrade -y
sudo apt install -y git python3 python3-venv python3-pip caddy
```

## 2. Install the application

```sh
sudo mkdir -p /opt/agw /var/lib/agw
sudo chown -R "$USER":"$USER" /opt/agw
git clone --branch deployment/secure-vps https://github.com/ArshakKhoshnevis/AGW-smart-garden-watering.git /opt/agw/repo
python3 -m venv /opt/agw/venv
/opt/agw/venv/bin/pip install --upgrade pip
/opt/agw/venv/bin/pip install -r /opt/agw/repo/AGW_web/requirements.txt
sudo useradd --system --home /var/lib/agw --shell /usr/sbin/nologin agw
sudo chown -R agw:agw /var/lib/agw
sudo chown -R root:agw /opt/agw
sudo chmod -R g+rX /opt/agw
```

The commands below use `agw.example.com`; replace it with your real domain.

## 3. Create server secrets

Generate a persistent Flask key and a separate ESP32 API token. Keep the output private:

```sh
python3 -c 'import secrets; print("AGW_SECRET_KEY=" + secrets.token_hex(32)); print("AGW_DEVICE_TOKEN=" + secrets.token_hex(32))'
```

Create `/etc/agw/agw.env` with the generated values:

```sh
sudo install -d -m 750 -o root -g agw /etc/agw
sudo nano /etc/agw/agw.env
```

Use this format (replace every example value):

```dotenv
AGW_SECRET_KEY=paste-generated-flask-key
AGW_DEVICE_TOKEN=paste-generated-device-token
AGW_DATABASE_URL=sqlite:////var/lib/agw/users.db
AGW_MAX_PUMP_RUN_SECONDS=5400
AGW_SESSION_COOKIE_SECURE=true
AGW_RATE_LIMIT_STORAGE_URI=memory://
```

Then protect the file:

```sh
sudo chown root:agw /etc/agw/agw.env
sudo chmod 640 /etc/agw/agw.env
```

Keep one Gunicorn worker while using the in-memory rate limiter. The pump state and sensor readings are in SQLite on persistent VPS storage; the rate limiter is only a small in-process login/API throttle.

## 4. Initialize the database and dashboard account

```sh
sudo -u agw env $(sudo cat /etc/agw/agw.env) /opt/agw/venv/bin/flask --app /opt/agw/repo/AGW_web/main.py init-db
sudo -u agw env $(sudo cat /etc/agw/agw.env) /opt/agw/venv/bin/flask --app /opt/agw/repo/AGW_web/main.py create-admin
```

Choose a unique dashboard password with at least 12 characters. To change it later, run the same `create-admin` command again.

## 5. Run Gunicorn with systemd

Create `/etc/systemd/system/agw.service`:

```ini
[Unit]
Description=AGW Flask application
After=network-online.target
Wants=network-online.target

[Service]
User=agw
Group=agw
WorkingDirectory=/opt/agw/repo/AGW_web
EnvironmentFile=/etc/agw/agw.env
ExecStart=/opt/agw/venv/bin/gunicorn --workers 1 --bind 127.0.0.1:8000 --access-logfile - --error-logfile - main:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Start it and confirm the local health check:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now agw
sudo systemctl status agw
curl http://127.0.0.1:8000/healthz
```

Expected health response: `{"status":"ok"}`.

## 6. Enable HTTPS with Caddy

Edit `/etc/caddy/Caddyfile`:

```caddyfile
agw.example.com {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
```

Replace the domain, then validate and reload:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Visit `https://agw.example.com` and sign in. If Caddy cannot issue the certificate, check DNS and confirm ports 80 and 443 are reachable.

## 7. Configure and flash the ESP32

On your development computer, copy `AGW/include/secrets.example.h` to `AGW/include/secrets.h`. This local file is ignored by Git. Set:

- `AGW_SERVER_URL` to `https://agw.example.com`
- Your garden Wi-Fi SSID and password
- `AGW_DEVICE_TOKEN` to the exact token from `/etc/agw/agw.env`
- `AGW_ROOT_CA_CERTIFICATE` to the public root CA certificate that issued the domain's HTTPS certificate

Do not use `setInsecure()`. The root CA is public certificate data, not a private key. Confirm the issuing chain from your certificate provider/Caddy before copying the PEM certificate. Keep `AGW_MAX_PUMP_RUN_MS` at `5400000UL` to match the 90-minute server cap. Build and upload using PlatformIO, then open Serial Monitor at 115200 baud.

A healthy device prints one status line every five seconds, for example:

```text
[2026-10-07 14:30:05] Data sent successfully: soil=[42%, 38%, 0%, 0%], server=OK, P1=OFF, P2=ON, P3=OFF, P4=OFF
```

This line contains Tehran local time, soil readings, server response, and all four pump states. Wi-Fi, clock, TLS, HTTP, and server-response failures are printed as status lines too. The ESP32 does not print the Wi-Fi password or device token.

## 8. Check operation and logs

```sh
sudo journalctl -u agw -f
sudo journalctl -u caddy -f
```

The dashboard health endpoint is `https://agw.example.com/healthz`. The protected `/states` route requires a logged-in dashboard session. Keep pump outputs OFF while first validating the site, TLS, ESP32 token, and sensor readings.

## Backups and updates

Back up `/var/lib/agw/users.db` regularly and keep a protected copy of `/etc/agw/agw.env` and the local ESP32 `secrets.h`. These contain operational state or secrets.

To update after changes reach the branch you deploy:

```sh
cd /opt/agw/repo
sudo -u "$USER" git pull
sudo /opt/agw/venv/bin/pip install -r /opt/agw/repo/AGW_web/requirements.txt
sudo systemctl restart agw
```

Do not run `git pull` as root in a checkout owned by another user; adjust repository ownership or the update user consistently if needed.
