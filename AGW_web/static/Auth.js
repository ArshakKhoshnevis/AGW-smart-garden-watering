function togglePass() {
    const pass = document.querySelector("#password");
    pass.type = pass.type === "text" ? "password" : "text";
}