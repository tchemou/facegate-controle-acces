// Runs before the first paint (loaded synchronously in <head>): applies the saved theme and sidebar state
// so the page never flashes the wrong colours or animates a collapse on every load.
(function () {
  try {
    var theme = localStorage.getItem("facegate-theme");
    if (theme === "light" || theme === "dark") document.documentElement.setAttribute("data-theme", theme);
    if (localStorage.getItem("facegate-sidebar") === "collapsed") document.documentElement.setAttribute("data-sidebar", "collapsed");
  } catch (err) { /* storage can be blocked: the system theme and the open sidebar apply */ }
})();
