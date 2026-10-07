import hmac
import os
from datetime import datetime, timedelta, timezone
from functools import wraps
from getpass import getpass

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import select
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
# Gunicorn will bind to localhost behind Caddy. Trust forwarded headers from that one proxy only.
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


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(25), unique=True, nullable=False)
    # Keep the existing column name so an existing users.db remains readable.
    password = db.Column(db.String(255), nullable=False)

    def set_pass(self, password):
        self.password = generate_password_hash(password)

    def check_pass(self, password):
        return check_password_hash(self.password, password)


class SensorSnapshot(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    moisture = db.Column(db.JSON, nullable=False, default=lambda: [0, 0, 0, 0])
    updated_at = db.Column(db.String(40), nullable=True)


class Pump(db.Model):
    # Database IDs are 1..4; the dashboard and ESP32 use indices 0..3.
    id = db.Column(db.Integer, primary_key=True)
    is_on = db.Column(db.Boolean, nullable=False, default=False)
    started_at = db.Column(db.String(40), nullable=True)
    last_run_at = db.Column(db.String(40), nullable=True)
    last_duration_seconds = db.Column(db.Integer, nullable=False, default=0)


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def format_duration(seconds):
    seconds = max(0, int(seconds or 0))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.0"


def finish_pump_run(pump, now):
    if pump.is_on and pump.started_at:
        started = parse_utc(pump.started_at)
        pump.last_duration_seconds = max(0, int((now - started).total_seconds()))
        pump.last_run_at = iso_utc(now)
    pump.is_on = False
    pump.started_at = None


def enforce_run_limits(now=None):
    now = now or utc_now()
    changed = False
    for pump in db.session.scalars(select(Pump)).all():
        started = parse_utc(pump.started_at)
        if pump.is_on and started and (now - started).total_seconds() >= MAX_PUMP_RUN_SECONDS:
            finish_pump_run(pump, now)
            changed = True
    if changed:
        db.session.commit()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            if request.path in {"/states"} or request.path.startswith("/pump-post/"):
                return jsonify({"status": "unauthorized"}), 401
            flash("Login first!")
            return redirect(url_for("auth"))
        return view(*args, **kwargs)
    return wrapped


def pumps_as_strings():
    pumps = db.session.scalars(select(Pump).order_by(Pump.id)).all()
    return ["on" if pump.is_on else "off" for pump in pumps]


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
    user = db.session.scalar(select(User).where(User.username == username)) if username else None

    if not user or not user.check_pass(password):
        # Use one message so the page does not reveal whether a username exists.
        flash("Username or password is incorrect.")
        return redirect(url_for("auth"))

    session.clear()
    session["username"] = user.username
    session.permanent = True
    return redirect(url_for("home"))


@app.route("/pump-post/<int:pump_id>", methods=["POST"])
@login_required
def pump_post(pump_id):
    if not 0 <= pump_id < 4:
        return jsonify({"status": "invalid_pump"}), 404

    data = request.get_json(silent=True) or {}
    desired_state = data.get("state")
    if desired_state not in {"on", "off"}:
        return jsonify({"status": "invalid_state"}), 400

    now = utc_now()
    enforce_run_limits(now)
    pump = db.session.get(Pump, pump_id + 1)
    if pump is None:
        return jsonify({"status": "not_initialized"}), 503

    if desired_state == "on" and not pump.is_on:
        pump.is_on = True
        pump.started_at = iso_utc(now)
    elif desired_state == "off" and pump.is_on:
        finish_pump_run(pump, now)

    db.session.commit()
    return jsonify({"status": "success", "pump_state": pumps_as_strings()})


@app.route("/states")
@login_required
def states():
    now = utc_now()
    enforce_run_limits(now)
    snapshot = db.session.get(SensorSnapshot, 1)
    pumps = db.session.scalars(select(Pump).order_by(Pump.id)).all()
    if snapshot is None or len(pumps) != 4:
        return jsonify({"status": "not_initialized"}), 503

    last_time = []
    for pump in pumps:
        last_time.append({
            "date": pump.last_run_at or "",
            "dur": format_duration(pump.last_duration_seconds),
        })

    return jsonify({
        "moist": snapshot.moisture,
        "pumpState": ["on" if pump.is_on else "off" for pump in pumps],
        "lastTime": last_time,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
    })


@app.route("/api-sensors", methods=["POST"])
@csrf.exempt
def api_sensor():
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
    if (
        not isinstance(soil, list)
        or len(soil) != 4
        or any(type(value) is not int or not 0 <= value <= 100 for value in soil)
    ):
        return jsonify({"status": "invalid_sensor_data"}), 400

    now = utc_now()
    enforce_run_limits(now)
    snapshot = db.session.get(SensorSnapshot, 1)
    pumps = db.session.scalars(select(Pump).order_by(Pump.id)).all()
    if snapshot is None or len(pumps) != 4:
        return jsonify({"status": "not_initialized"}), 503

    snapshot.moisture = soil
    snapshot.updated_at = iso_utc(now)
    db.session.commit()
    return jsonify({f"pump{index + 1}": pump.is_on for index, pump in enumerate(pumps)})


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
    """Create the initial database tables and four safe, OFF pump records."""
    db.create_all()
    if db.session.get(SensorSnapshot, 1) is None:
        db.session.add(SensorSnapshot(id=1, moisture=[0, 0, 0, 0]))
    for pump_id in range(1, 5):
        if db.session.get(Pump, pump_id) is None:
            db.session.add(Pump(id=pump_id, is_on=False))
    db.session.commit()
    click.echo("AGW database initialized; all pumps are OFF.")


@app.cli.command("create-admin")
def create_admin():
    """Create or replace the single dashboard user's password."""
    username = click.prompt("Username").strip()
    if not username or len(username) > 25:
        raise click.ClickException("Username must contain 1 to 25 characters.")

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Repeat password: ")
    if len(password) < 12:
        raise click.ClickException("Use a password with at least 12 characters.")
    if password != confirmation:
        raise click.ClickException("Passwords do not match.")

    user = db.session.scalar(select(User).where(User.username == username))
    if user is None:
        user = User(username=username, password="")
    user.set_pass(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Dashboard account '{username}' is ready.")


if __name__ == "__main__":
    # Local development only. Production runs Gunicorn behind Caddy.
    app.run(host="127.0.0.1", port=5000, debug=False)
