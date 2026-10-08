import hmac
import os
import secrets
import time
from datetime import datetime, timezone
from functools import wraps
from getpass import getpass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import click
from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
secret_key = os.environ.get("AGW_SECRET_KEY")
if not secret_key:
    raise RuntimeError("Set AGW_SECRET_KEY to a stable random secret before starting AGW.")

app.config.update(
    SECRET_KEY = secret_key,
    SQLALCHEMY_DATABASE_URI = os.environ.get("AGW_DATABASE_URL", "sqlite:///users.db"),
    SQLALCHEMY_TRACK_MODIFICATIONS = False,
    SESSION_COOKIE_SECURE = os.environ.get("AGW_SESSION_COOKIE_SECURE", "true").lower() == "true",
    SESSION_COOKIE_HTTPONLY = True,
    SESSION_COOKIE_SAMESITE = "Lax",
    MAX_CONTENT_LENGTH = 4096,
)

# Production runs behind one local reverse proxy (Caddy).
app.wsgi_app = ProxyFix(app.wsgi_app, x_for = 1, x_proto = 1, x_host = 1)

db = SQLAlchemy(app)
csrf = CSRFProtect(app)

try:
    LOG_TIMEZONE = ZoneInfo(os.environ.get("AGW_LOG_TIMEZONE", "Asia/Tehran"))
except ZoneInfoNotFoundError:
    LOG_TIMEZONE = timezone.utc

DEVICE_USERNAME = os.environ.get("AGW_DEVICE_USERNAME", "")
DEVICE_PASSWORD = os.environ.get("AGW_DEVICE_PASSWORD", "")
TOKEN_LIFETIME_SECONDS = 600

# Keep the garden's live state in memory, like the original project.
pump_state = ["off", "off", "off", "off"]
moist = [0, 0, 0, 0]
start = [None, None, None, None]
last_time = [{"date": "", "dur": "00:00:00.0"} for _ in range(4)]
current_token = ""
token_expires = 0
last_device_update = None


class User(db.Model):
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(25), unique = True, nullable = False)
    password = db.Column(db.String(150), nullable = False)

    def set_pass(self, password):
        self.password = generate_password_hash(password)

    def check_pass(self, password):
        return check_password_hash(self.password, password)


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


def valid_device_token(token):
    return bool(
        current_token
        and token
        and hmac.compare_digest(token, current_token)
        and time.time() < token_expires
    )


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


@app.route("/login-post", methods = ["POST"])
def login_post():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    user = User.query.filter_by(username = username).first() if username else None

    if not user or not user.check_pass(password):
        flash("Username or password is incorrect.")
        return redirect(url_for("auth"))

    session.clear()
    session["username"] = user.username
    return redirect(url_for("home"))


@app.route("/login-esp32", methods = ["POST"])
@csrf.exempt
def login_esp32():
    global current_token, token_expires
    data = request.get_json(silent = True)
    if not isinstance(data, dict):
        return jsonify({"status": "failed"}), 400

    username = data.get("username", "")
    password = data.get("password", "")
    credentials_match = (
        DEVICE_USERNAME
        and DEVICE_PASSWORD
        and hmac.compare_digest(str(username).encode("utf-8"), DEVICE_USERNAME.encode("utf-8"))
        and hmac.compare_digest(str(password).encode("utf-8"), DEVICE_PASSWORD.encode("utf-8"))
    )
    if not credentials_match:
        return jsonify({"status": "failed"}), 401

    current_token = secrets.token_hex(32)
    token_expires = time.time() + TOKEN_LIFETIME_SECONDS
    return jsonify({"status": "success", "token": current_token})


@app.route("/pump-post/<int:pump_id>", methods = ["POST"])
@login_required
def pump_post(pump_id):
    if not 0 <= pump_id < 4:
        return jsonify({"status": "invalid_pump"}), 404

    global pump_state, start, last_time
    if pump_state[pump_id] == "off":
        pump_state[pump_id] = "on"
        start[pump_id] = datetime.now(timezone.utc)
    else:
        pump_state[pump_id] = "off"
        stopped = datetime.now(timezone.utc)
        if start[pump_id]:
            duration = (stopped - start[pump_id]).total_seconds()
            hours, remainder = divmod(int(duration), 3600)
            minutes, seconds = divmod(remainder, 60)
            last_time[pump_id] = {
                "date": stopped.astimezone(LOG_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S"),
                "dur": f"{hours:02d}:{minutes:02d}:{seconds:02d}.0",
            }
        start[pump_id] = None

    return jsonify({"status": "success", "pump_state": pump_state})


@app.route("/states")
@login_required
def states():
    now = datetime.now(timezone.utc)
    return jsonify({
        "moist": moist,
        "pumpState": pump_state,
        "lastTime": last_time,
        "date": now.astimezone(LOG_TIMEZONE).strftime("%Y-%m-%d"),
        "time": now.astimezone(LOG_TIMEZONE).strftime("%H:%M:%S"),
        "timestamp": now.isoformat(timespec = "seconds").replace("+00:00", "Z"),
        "lastUpdate": (
            last_device_update.isoformat(timespec = "seconds").replace("+00:00", "Z")
            if last_device_update else None
        ),
    })


@app.route("/api-sensors", methods = ["POST"])
@csrf.exempt
def api_sensor():
    global moist, last_device_update
    data = request.get_json(silent = True)
    token = request.headers.get("Authorization", "")
    if token.startswith("Bearer "):
        token = token[7:]
    else:
        token = ""

    if not valid_device_token(token):
        return jsonify({"status": "unauthorized"}), 401

    soil = data.get("soil") if isinstance(data, dict) else None
    if (
        not isinstance(soil, list)
        or len(soil) != 4
        or any(type(value) is not int or not 0 <= value <= 100 for value in soil)
    ):
        return jsonify({"status": "invalid_sensor_data"}), 400

    moist = soil
    last_device_update = datetime.now(timezone.utc)
    local_time = last_device_update.astimezone(LOG_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")
    states = ", ".join(f"P{i + 1} = {state.upper()}" for i, state in enumerate(pump_state))
    app.logger.info(
        "[%s] ESP32 data received: soil = [%s], %s",
        local_time,
        ", ".join(f"{value}%" for value in moist),
        states,
    )
    return jsonify({f"pump{i + 1}": pump_state[i] == "on" for i in range(4)})


@app.route("/logout", methods = ["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth"))


@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.cli.command("init-db")
def init_db():
    """Create the existing SQLite user-account table."""
    db.create_all()
    click.echo("AGW account database initialized.")


@app.cli.command("create-admin")
def create_admin():
    """Create or update the dashboard login."""
    username = click.prompt("Username").strip()
    if not username or len(username) > 25:
        raise click.ClickException("Username must contain 1 to 25 characters.")

    password = getpass("Password (minimum 12 characters): ")
    confirmation = getpass("Repeat password: ")
    if len(password) < 12:
        raise click.ClickException("Use a password with at least 12 characters.")
    if password != confirmation:
        raise click.ClickException("Passwords do not match.")

    user = User.query.filter_by(username = username).first()
    if user is None:
        user = User(username = username, password = "")
    user.set_pass(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Dashboard account '{username}' is ready.")


