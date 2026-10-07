let activeLink = null;
let activeId = null;
let lastLoggedUpdate = null;
const logLines = [];

let arr = {
    pumpState: [],
    moist: [],
    lastTime: [],
    date: "",
    time: ""
};

function addLogLine(line) {
    console.log(line);
    logLines.push(line);
    if (logLines.length > 50) logLines.shift();

    const log = document.getElementById("esp-log");
    if (log) log.textContent = logLines.join("\n");
}

async function togglePump(id) {
    const csrfToken = document.querySelector('meta[name="csrf-token"]').content;

    try {
        const response = await fetch(`/pump-post/${id}`, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken }
        });
        if (!response.ok) {
            throw new Error(`Pump update failed (HTTP ${response.status})`);
        }

        const data = await response.json();
        addLogLine(`[${new Date().toLocaleString()}] Pump command updated: ${data.pump_state.join(", ")}`);
        await Update();
    } catch (error) {
        console.error(error);
        await Update();
    }
}

function timeSince(lastTime, rightNow) {
    if (!lastTime) return "No recorded watering";
    const last = new Date(lastTime);
    const now = new Date(rightNow);
    if (Number.isNaN(last.getTime()) || Number.isNaN(now.getTime())) return "No recorded watering";

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

function updateEspStatus(lastUpdate) {
    const status = document.getElementById("esp-status");
    if (!status) return;

    if (!lastUpdate) {
        status.textContent = "ESP32: waiting for data";
        status.className = "esp-status unknown";
        return;
    }

    const updatedAt = new Date(lastUpdate);
    if (Number.isNaN(updatedAt.getTime())) {
        status.textContent = "ESP32: update time unavailable";
        status.className = "esp-status unknown";
        return;
    }

    const ageSeconds = Math.max(0, Math.floor((Date.now() - updatedAt.getTime()) / 1000));
    if (ageSeconds <= 15) {
        status.textContent = `ESP32: online · updated ${ageSeconds}s ago`;
        status.className = "esp-status online";
    } else {
        status.textContent = `ESP32: no recent data · last update ${updatedAt.toLocaleTimeString()}`;
        status.className = "esp-status offline";
    }
}

function logDeviceUpdate(data) {
    if (!data.lastUpdate || data.lastUpdate === lastLoggedUpdate) return;
    lastLoggedUpdate = data.lastUpdate;

    const time = new Date(data.lastUpdate).toLocaleString();
    const soil = data.moist.map(value => `${value}%`).join(", ");
    const pumps = data.pumpState
        .map((state, index) => `P${index + 1}=${state.toUpperCase()}`)
        .join(", ");
    addLogLine(`[${time}] ESP32 data received: soil=[${soil}], pumps=[${pumps}]`);
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

    document.querySelectorAll(".moist").forEach((heading, index) => {
        if (arr.moist[index] !== undefined) heading.textContent = `${arr.moist[index]}%`;
    });

    arr.pumpState.forEach((state, index) => {
        const checkbox = document.getElementById(`checkbox-${index}`);
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
        arr.date = data.date;
        arr.time = data.time;

        updateEspStatus(data.lastUpdate);
        logDeviceUpdate(data);
        UpdateUI();
    } catch (error) {
        const status = document.getElementById("esp-status");
        if (status) {
            status.textContent = "Web app: unable to refresh";
            status.className = "esp-status offline";
        }
        console.error(error);
    }
}

document.querySelectorAll(".hinfo").forEach((link, index) => {
    link.addEventListener("click", event => {
        event.preventDefault();
        const infoDiv = document.querySelector(".info");
        infoDiv.querySelectorAll("details").forEach(detail => detail.open = false);

        if (activeLink === link) {
            link.textContent = "more";
            infoDiv.style.display = "none";
            activeLink = null;
            activeId = null;
            return;
        }

        if (activeLink) activeLink.textContent = "more";
        infoDiv.style.display = "flex";
        document.querySelectorAll(".hinfo").forEach(item => item.textContent = "more");
        link.textContent = "less";
        activeLink = link;
        activeId = index;
        UpdateUI();
    });
});

Update();
setInterval(Update, 2000);
