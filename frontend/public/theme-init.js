// Applies the saved light/dark theme before the first paint to avoid a flash.
// Kept as a static file (not inline) so a strict Content-Security-Policy can allow it.
(function () {
  try {
    var stored = window.localStorage.getItem("opspilot.theme");
    var dark =
      stored === "dark" ||
      ((stored === null || stored === "system") && window.matchMedia("(prefers-color-scheme: dark)").matches);
    if (dark) document.documentElement.classList.add("dark");
    document.documentElement.style.colorScheme = dark ? "dark" : "light";
  } catch (e) {
    // Ignore: the app applies the theme once it starts.
  }
})();
