import hmac
import os
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from getpass import getpass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash


MAX_PUMP_RUN_SECONDS = int(os.environ.get("AGW_MAX_PUMP_RUN_SECONDS", "5400"))
if not 1 <= MAX_PUMP_RUN_SECONDS <= 5400:
    raise RuntimeError("AGW_MAX_PUMP_RUN_SECONDS must be between 1 and 5400 seconds (90 minutes).")

app = Flask(__name__)
secret_key = os.environ.get("AGW_SECRET_KEY")
if not secret_key:
    raise RuntimeError("Set AGW_SECRET_KEY to a persistent random value before starting AGW.")

app.config.update(
    SECRET_KEY=secret_key,
    SQLALCHEMY_DATABASE_URI=os.environ.get("AGW_DATABASE_URL", "sqlite:///users.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS=False,
    SESSION_COOKIE_SECURE=os.environ.get("AGW_SESSION_COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=4096,
)
# Gunicorn binds to localhost behind Caddy. Trust forwarded headers from that one proxy only.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.environ.get("AGW_RATE_LIMIT_STORAGE_URI", "memory://"),
)

DEVICE_TOKEN = os.environ.get("AGW_DEVICE_TOKEN", "")
try:
    LOG_TIMEZONE = ZoneInfo(os.environ.get("AGW_LOG_TIMEZONE", "Asia/Tehran"))
except ZoneInfoNotFoundError:
    LOG_TIMEZONE = timezone.utc
    app.logger.warning("Unknown AGW_LOG_TIMEZONE; heartbeat logs will use UTC.")

# Live garden data stays in memory, matching the original app's simple state model.
pump_state = ["off", "off", "off", "off"]
pump_started_at = [None, None, None, None]
moist = [0, 0, 0, 0]
reported_pumps = [False, False, False, False]
last_time = [{"date": "", "dur": "00:00:00.0"} for _ in range(4)]
device_last_seen = None


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(25), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def set_pass(self, password):
        self.password = generate_password_hash(password)

    def check_pass(self, password):
        return check_password_hash(self.password, password)


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def log_local_time(value):
    return value.astimezone(LOG_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.0"


def stop_pump(index, now):
    global pump_state, pump_started_at, last_time
    if pump_state[index] == "on" and pump_started_at[index] is not None:
        last_time[index] = {
            "date": iso_utc(now),
            "dur": format_duration((now - pump_started_at[index]).total_seconds()),
        }
    pump_state[index] = "off"
    pump_started_at[index] = None


def enforce_run_limits(now=None):
    now = now or utc_now()
    for index in range(4):
        started = pump_started_at[index]
        if (
            pump_state[index] == "on"
            and started is not None
            and (now - started).total_seconds() >= MAX_PUMP_RUN_SECONDS
        ):
            stop_pump(index, now)
            app.logger.warning(
                "[%s] Pump %d stopped by the 90-minute server safety limit.",
                log_local_time(now), index + 1,
            )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            if request.path == "/states" or request.path.startswith("/pump-post/"):
                return jsonify({"status": "unauthorized"}), 401
            flash("Login first!")
            return redirect(url_for("auth"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/")
@app.route("/Auth")
def auth():
    if session.get("username"):
        return redirect(url_for("home"))
    return render_template("Auth.html")


@app.route("/home")
@login_required
def home():
    return render_template("Home.html")


@app.route("/login-post", methods=["POST"])
@limiter.limit("5 per minute")
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = User.query.filter_by(username=username).first() if username else None

    if not user or not user.check_pass(password):
        flash("Username or password is incorrect.")
        return redirect(url_for("auth"))

    session.clear()
    session["username"] = user.username
    session.permanent = True
    return redirect(url_for("home"))


@app.route("/pump-post/<int:pump_id>", methods=["POST"])
@login_required
def pump_post(pump_id):
    global pump_state, pump_started_at, last_time
    if not 0 <= pump_id < 4:
        return jsonify({"status": "invalid_pump"}), 404

    data = request.get_json(silent=True) or {}
    desired_state = data.get("state")
    if desired_state not in {"on", "off"}:
        return jsonify({"status": "invalid_state"}), 400

    now = utc_now()
    enforce_run_limits(now)
    if desired_state == "on" and pump_state[pump_id] == "off":
        pump_state[pump_id] = "on"
        pump_started_at[pump_id] = now
    elif desired_state == "off" and pump_state[pump_id] == "on":
        stop_pump(pump_id, now)

    return jsonify({"status": "success", "pump_state": pump_state})


@app.route("/states")
@login_required
def states():
    now = utc_now()
    enforce_run_limits(now)
    return jsonify({
        "moist": moist,
        "pumpState": pump_state,
        "reportedPumpState": reported_pumps,
        "lastTime": last_time,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "timestamp": iso_utc(now),
        "deviceLastSeen": iso_utc(device_last_seen) if device_last_seen else None,
    })


@app.route("/api-sensors", methods=["POST"])
@limiter.limit("30 per minute")
@csrf.exempt
def api_sensor():
    global moist, reported_pumps, device_last_seen, pump_state
    if not DEVICE_TOKEN:
        return jsonify({"status": "device_not_configured"}), 503

    authorization = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not authorization.startswith(prefix) or not hmac.compare_digest(
        authorization[len(prefix):], DEVICE_TOKEN
    ):
        return jsonify({"status": "unauthorized"}), 401

    data = request.get_json(silent=True)
    soil = data.get("soil") if isinstance(data, dict) else None
    device_pumps = data.get("pumpState") if isinstance(data, dict) else None
    boot = data.get("boot", False) if isinstance(data, dict) else False
    if (
        not isinstance(soil, list)
        or len(soil) != 4
        or any(type(value) is not int or not 0 <= value <= 100 for value in soil)
        or not isinstance(device_pumps, list)
        or len(device_pumps) != 4
        or any(type(value) is not bool for value in device_pumps)
        or type(boot) is not bool
    ):
        return jsonify({"status": "invalid_sensor_data"}), 400

    now = utc_now()
    enforce_run_limits(now)
    if boot:
        # The ESP32 powers relays OFF at startup; discard commands from before the reboot.
        for index in range(4):
            stop_pump(index, now)

    moist = soil
    reported_pumps = device_pumps
    device_last_seen = now

    pump_summary = ", ".join(
        f"P{index + 1}={'ON' if is_on else 'OFF'}"
        for index, is_on in enumerate(device_pumps)
    )
    soil_summary = ", ".join(f"{value}%" for value in soil)
    app.logger.info(
        "[%s] ESP32 heartbeat received: soil=[%s], %s, server=OK, boot=%s",
        log_local_time(now), soil_summary, pump_summary, boot,
    )
    return jsonify({f"pump{index + 1}": pump_state[index] == "on" for index in range(4)})


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth"))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.cli.command("init-db")
def init_db():
    """Create the small SQLite database used for dashboard accounts."""
    db.create_all()
    click.echo("AGW account database initialized.")


@app.cli.command("create-admin")
def create_admin():
    """Create or replace the dashboard user's password."""
    username = click.prompt("Username").strip()
    if not username or len(username) > 25:
        raise click.ClickException("Username must contain 1 to 25 characters.")

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Repeat password: ")
    if len(password) < 12:
        raise click.ClickException("Use a password with at least 12 characters.")
    if password != confirmation:
        raise click.ClickException("Passwords do not match.")

    user = User.query.filter_by(username=username).first()
    if user is None:
        user = User(username=username, password="")
    user.set_pass(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Dashboard account '{username}' is ready.")


if __name__ == "__main__":
    # Local development only. Production runs Gunicorn behind Caddy.
    app.run(host="127.0.0.1", port=5000, debug=False)
