let activeLink = null, activeId = null;

let arr = {
    pumpState: [],
    moist: [],
    lastTime: [],
    date: "",
    time: ""
};

function togglePump(id) {
    fetch(`/pump-post/${id}`, { method: 'POST' })
        .then(response => response.json())
        .then(data => console.log(data))
        .catch(err => console.error(err));
}

function timeSince(lastTime, rightNow) {
    const last = new Date(lastTime);
    const now = new Date(rightNow);
    const delta = (now - last) / 1000;

    if(delta <= 60)
        return "just now!";
    if(delta <= 3600)
        return Math.floor(delta / 60) + " min(s) ago";
    if(delta <= 3600 * 24)
        return Math.floor(delta / 3600) + " hour(s) ago";
    if(delta <= 3600 * 24 * 30)
        return Math.floor(delta / (3600 * 24)) + " day(s) ago";
    if(delta <= 3600 * 24 * 30 * 12)
        return Math.floor(delta / (3600 * 24 * 30)) + " month(s) ago";
    return Math.floor(delta / (3600 * 24 * 30 * 12)) + " year(s) ago";
}

function calDuration(dur) {
    const parts = dur.split(':');
    const hrs = parseInt(parts[0]);
    const mins = parseInt(parts[1]);
    const secs = parseInt(parts[2].split('.')[0]);

    let res = '';
    if(hrs > 0)
        res += hrs + ' hour(s) ';
    if(mins > 0)
        res += mins + ' minute(s) ';
    if(secs > 0)
        res += secs + ' second(s) ';

    return res;
}

function UpdateUI() {
    if(activeId !== null) {
        const infoDiv = document.querySelector('.info');
        // const details = infoDiv.querySelector('details');
        // const isOpen = details ? details.open : false;

        // infoDiv.innerHTML = `

        //     <div class = "info-content">
        //         <h1 class = "infoMoist">Moisture: <span>${arr.moist[activeId]}%</span></h1>

        //         <details id = "LastTime">
        //             <summary>Last Time</summary>
        //             <h1 class = "infoLastTime">Last irrigation: ${timeSince(arr.lastTime[activeId]["date"], arr.date + " " + arr.time)}</h1>
        //             <h1 class = "infoLastTime">Duration: ${calDuration(arr.lastTime[activeId]["dur"])}</h1>
        //         </details>

        //         <h1 class = "infoPumpState">Pump is: <span class = "${arr.pumpState[activeId]}">${arr.pumpState[activeId]}</span></h1>
        //     </div>

        //     <div class = "info-footer">
        //         <h1 class = "infoDate">${arr.date}</h1>
        //         <h1 class = "infoTime">${arr.time}</h1>
        //     </div>
        // `;

        // const newDetails = infoDiv.querySelector('details');
        // newDetails.open = isOpen;

        infoDiv.querySelector('.infoMoist span').textContent = `${arr.moist[activeId]}%`;
        infoDiv.querySelector('.infoLastTime:nth-of-type(1)').textContent =
            `Last irrigation: ${timeSince(arr.lastTime[activeId]["date"], arr.date + " " + arr.time)}`;
        infoDiv.querySelector('.infoLastTime:nth-of-type(2)').textContent =
            `Duration: ${calDuration(arr.lastTime[activeId]["dur"])}`;
        infoDiv.querySelector('.infoPumpState span').textContent = arr.pumpState[activeId];
        infoDiv.querySelector('.infoPumpState span').className = arr.pumpState[activeId];

        infoDiv.querySelector('.infoDate').textContent = arr.date;
        infoDiv.querySelector('.infoTime').textContent = arr.time;
    }

    document.querySelectorAll('.moist').forEach((h, i) => h.textContent = `${arr.moist[i]}%`);

    arr.pumpState.forEach((state, i) => {
        const checkbox = document.getElementById(`checkbox-${i}`);
        checkbox.checked = (state === "on");
    });
}

function Update() {
    fetch("/states")
        .then(response => response.json())
        .then(data => {
            arr.pumpState = data.pumpState;
            arr.moist = data.moist;
            arr.lastTime = data.lastTime;
            arr.date = data.date;
            arr.time = data.time;

            console.log("Updated:", arr, " activeID: ", activeId);

            UpdateUI();
        })
        .catch(err => console.error(err));
}

document.querySelectorAll('.hinfo').forEach((link, index) => {
    link.addEventListener('click', () => {
        const infoDiv = document.querySelector('.info');

        infoDiv.querySelectorAll('details').forEach(d => d.open = false);

        if(activeLink === link) {
            link.textContent = "more";
            infoDiv.style.display = "none";
            activeLink = null;
            activeId = null;
            return;
        }

        if(activeLink)
            activeLink.textContent = "more";

        infoDiv.style.display = 'flex';
        document.querySelectorAll('.hinfo').forEach(l => l.textContent = "more");
        link.textContent = "less";
        activeLink = link;
        activeId = index;
        console.log("\n" + activeId)
    });
});

setInterval(Update, 2000);