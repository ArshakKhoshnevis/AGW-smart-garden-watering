let activeLink = null, activeId = null;
let lastLoggedHeartbeat = null;
const heartbeatLogLines = [];

let arr = {
    pumpState: [],
    moist: [],
    lastTime: [],
    date: "",
    time: "",
    timestamp: ""
};

async function togglePump(id) {
    const checkbox = document.getElementById(`checkbox-${id}`);
    const desiredState = checkbox.checked ? "on" : "off";
    const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

    try {
        const response = await fetch(`/pump-post/${id}`, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken
            },
            body: JSON.stringify({ state: desiredState })
        });
        if (!response.ok) {
            throw new Error(`Pump command failed (HTTP ${response.status})`);
        }
        await response.json();
        await Update();
    } catch (err) {
        console.error(err);
        await Update();
    }
}

function timeSince(lastTime, rightNow) {
    if (!lastTime) return "No recorded watering";
    const last = new Date(lastTime);
    const now = new Date(rightNow);
    if (Number.isNaN(last.getTime()) || Number.isNaN(now.getTime())) {
        return "No recorded watering";
    }
    const delta = Math.max(0, (now - last) / 1000);
    if (delta <= 60) return "just now!";
    if (delta <= 3600) return Math.floor(delta / 60) + " min(s) ago";
    if (delta <= 3600 * 24) return Math.floor(delta / 3600) + " hour(s) ago";
    if (delta <= 3600 * 24 * 30) return Math.floor(delta / (3600 * 24)) + " day(s) ago";
    if (delta <= 3600 * 24 * 30 * 12) return Math.floor(delta / (3600 * 24 * 30)) + " month(s) ago";
    return Math.floor(delta / (3600 * 24 * 30 * 12)) + " year(s) ago";
}

function calDuration(dur) {
    if (!dur) return "--";
    const parts = dur.split(":");
    const hrs = parseInt(parts[0], 10);
    const mins = parseInt(parts[1], 10);
    const secs = parseInt(parts[2].split(".")[0], 10);
    let res = "";
    if (hrs > 0) res += hrs + " hour(s) ";
    if (mins > 0) res += mins + " minute(s) ";
    if (secs > 0) res += secs + " second(s) ";
    return res || "0 seconds";
}

function updateDeviceStatus(lastSeen) {
    const status = document.getElementById("device-status");
    if (!status) return;

    if (!lastSeen) {
        status.textContent = "ESP32: waiting for first heartbeat";
        status.className = "device-status device-unknown";
        return;
    }

    const seenAt = new Date(lastSeen);
    if (Number.isNaN(seenAt.getTime())) {
        status.textContent = "ESP32: heartbeat time unavailable";
        status.className = "device-status device-unknown";
        return;
    }

    const ageSeconds = Math.max(0, Math.floor((Date.now() - seenAt.getTime()) / 1000));
    if (ageSeconds <= 15) {
        status.textContent = `ESP32: online · last update ${ageSeconds}s ago`;
        status.className = "device-status device-online";
    } else {
        status.textContent = `ESP32: no recent response · last seen ${seenAt.toLocaleString()}`;
        status.className = "device-status device-offline";
    }
}

function logNewHeartbeat(data) {
    const lastSeen = data.deviceLastSeen;
    if (!lastSeen || lastSeen === lastLoggedHeartbeat) return;
    lastLoggedHeartbeat = lastSeen;

    const time = new Date(lastSeen).toLocaleString();
    const soil = data.moist.map(value => `${value}%`).join(", ");
    const pumps = data.pumpState
        .map((state, index) => `P${index + 1}=${state.toUpperCase()}`)
        .join(", ");
    const line = `[${time}] ESP32 heartbeat received: soil=[${soil}], ${pumps}, server=OK`;

    console.info(line);
    heartbeatLogLines.push(line);
    if (heartbeatLogLines.length > 50) heartbeatLogLines.shift();

    const log = document.getElementById("esp32-log");
    if (log) log.textContent = heartbeatLogLines.join("\\n");
}

function UpdateUI() {
    if (activeId !== null && arr.moist.length === 4 && arr.lastTime.length === 4) {
        const infoDiv = document.querySelector(".info");
        infoDiv.querySelector(".infoMoist span").textContent = `${arr.moist[activeId]}%`;
        infoDiv.querySelector(".infoLastTime:nth-of-type(1)").textContent =
            `Last irrigation: ${timeSince(arr.lastTime[activeId].date, new Date())}`;
        infoDiv.querySelector(".infoLastTime:nth-of-type(2)").textContent =
            `Duration: ${calDuration(arr.lastTime[activeId].dur)}`;
        infoDiv.querySelector(".infoPumpState span").textContent = arr.pumpState[activeId];
        infoDiv.querySelector(".infoPumpState span").className = arr.pumpState[activeId];
        infoDiv.querySelector(".infoDate").textContent = arr.date;
        infoDiv.querySelector(".infoTime").textContent = arr.time;
    }

    document.querySelectorAll(".moist").forEach((h, i) => {
        if (arr.moist[i] !== undefined) h.textContent = `${arr.moist[i]}%`;
    });
    arr.pumpState.forEach((state, i) => {
        const checkbox = document.getElementById(`checkbox-${i}`);
        if (checkbox) checkbox.checked = state === "on";
    });
}

async function Update() {
    try {
        const response = await fetch("/states", { cache: "no-store" });
        if (!response.ok) throw new Error(`Dashboard update failed (HTTP ${response.status})`);
        const data = await response.json();
        arr.pumpState = data.pumpState;
        arr.moist = data.moist;
        arr.lastTime = data.lastTime;
        updateDeviceStatus(data.deviceLastSeen);
        logNewHeartbeat(data);
        const serverNow = new Date(data.timestamp);
        arr.timestamp = data.timestamp;
        arr.date = `${serverNow.getFullYear()}-${String(serverNow.getMonth() + 1).padStart(2, "0")}-${String(serverNow.getDate()).padStart(2, "0")}`;
        arr.time = `${String(serverNow.getHours()).padStart(2, "0")}:${String(serverNow.getMinutes()).padStart(2, "0")}:${String(serverNow.getSeconds()).padStart(2, "0")}`;
        UpdateUI();
    } catch (err) {
        const status = document.getElementById("device-status");
        if (status) {
            status.textContent = "Dashboard: unable to refresh ESP32 status";
            status.className = "device-status device-offline";
        }
        console.error(err);
    }
}

document.querySelectorAll(".hinfo").forEach((link, index) => {
    link.addEventListener("click", (event) => {
        event.preventDefault();
        const infoDiv = document.querySelector(".info");
        infoDiv.querySelectorAll("details").forEach(d => d.open = false);

        if (activeLink === link) {
            link.textContent = "more";
            infoDiv.style.display = "none";
            activeLink = null;
            activeId = null;
            return;
        }
        if (activeLink) activeLink.textContent = "more";
        infoDiv.style.display = "flex";
        document.querySelectorAll(".hinfo").forEach(l => l.textContent = "more");
        link.textContent = "less";
        activeLink = link;
        activeId = index;
        UpdateUI();
    });
});

Update();
setInterval(Update, 2000);
