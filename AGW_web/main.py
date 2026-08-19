from flask import Flask, request, jsonify, render_template, session, redirect, flash, url_for
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import secrets
# import base64
# from SECRETS import SECRET_KEY
import time
from datetime import datetime

app = Flask(__name__)
app.secret_key = secrets.token_hex(32)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

pump_state = ["off", "off", "off", "off"]
moist = [0, 0, 0, 0]
start = [0, 0, 0, 0]
last_time = [{"date": "", "dur": "00:00:00.0"} for _ in range(4)]
TOKENS = {}
token_expiry = 600
current_token = ""
expires = None

class User(db.Model) :
    id = db.Column(db.Integer, primary_key = True)
    username = db.Column(db.String(25), unique = True, nullable = False)
    password = db.Column(db.String(150), nullable = False)

    def set_pass(self, password) :
        self.password_hash = generate_password_hash(password)
        self.password = self.password_hash

    def check_pass(self, password) :
        return check_password_hash(self.password, password)


@app.route('/')
@app.route('/Auth')
def auth() :
    return render_template("Auth.html")

@app.route('/home')
def home() :
    if not session:
        flash("Login first!")
        return redirect('/Auth')
    return render_template("Home.html")

@app.route('/login-post', methods = ['POST'])
def login_post() :
    username = request.form['username']
    password = request.form['password']
    dir = '/Auth'
    if not username:
        flash('Enter your username!')
        return redirect(dir)
    if not password:
        flash('Enter your password!')
        return redirect(dir)
    user = User.query.filter_by(username = username).first()
    if user :
        if user.check_pass(password) :
            session['username'] = username
            flash("Logged in Successfully")
            dir = '/home'
        else :
            flash("Wrong username or password.")
    else :
        flash("No such username was found")
    return redirect(dir)

@app.route('/login-esp32', methods = ['POST'])
def login_esp32() :
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')

    user = User.query.filter_by(username = username).first()
    if user and user.check_pass(password) :
        global current_token
        global expires

        current_token = secrets.token_hex(16)
        expires = time.time() + token_expiry

        # cipher = AES.new(SECRET_KEY, AES.MODE_ECB)
        # ct_bytes = cipher.encrypt(pad(token.encode(), AES.block_size))
        # ct_b64 = base64.b64encode(ct_bytes).decode('utf-8')

        print("token refreshed")

        return jsonify({"status": "success", "token": current_token})
    return jsonify({"status": "failed"}), 401


def isValidToken(token) :
    return (token == current_token and expires > time.time())

@app.route('/pump-post/<int:id>', methods = ['POST'])
def pump_post(id) :
    global pump_state
    global last_time
    global start

    if(pump_state[id] == "off") :
        pump_state[id] = "on"
        start[id] = datetime.now()
    else :
        pump_state[id] = "off"
        last_time[id]["date"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        last_time[id]["dur"] = str(datetime.now() - start[id])

    print(pump_state)
    return jsonify({"status": "success", "pump_state": pump_state})

@app.route('/states')
def states() :
    now = datetime.now()
    return jsonify({
        "moist": moist,
        "pumpState": pump_state,
        "lastTime": last_time,
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S")
        })

@app.route('/api-sensors', methods = ['POST'])
def api_sensor():
    data = request.get_json()
    token = request.headers.get('Authorization', '').replace("Bearer ", "")

    if not isValidToken(token):
        return jsonify({"status": "unauthorized"}), 401

    print("Received sensor data: ", data["soil"])

    global moist
    for i in range(4):
        moist[i] = data["soil"][i]

    response = {f"pump{i + 1}": (pump_state[i] == "on") for i in range(4)}
    return jsonify(response)


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/Auth')



if (__name__ == '__main__'):
    # with app.app_context():
        # db.create_all()
        # newuser = User(username = "")
        # newuser.set_pass("")
        # db.session.add(newuser)
        # db.session.commit()
    app.run(host = '0.0.0.0', port = 5000)