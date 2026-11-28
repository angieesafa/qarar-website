/*
 * Qarar live stats — paste before </body> on qararmarketing.com:
 *   <script src="/live-stats.js" defer></script>
 *
 * Then mark any number on the page with data-stat. The text already in the
 * element stays as the fallback if stats.json can't be loaded.
 *
 *   <div class="big" data-stat="total">191K</div>                   -> all platforms
 *   <b data-stat="instagram">120K</b>                               -> one platform
 *   <span data-stat="updated"></span>                               -> "Updated 28 Nov 2026"
 *   <span data-stat="window"></span>                                -> "90 days"
 *
 * Optional: data-format="full" shows 191,234 instead of 191K.
 */
(function () {
  var STATS_URL = "/stats.json"; // change if stats.json is hosted elsewhere

  function compact(n) {
    if (n >= 1e6) return (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
    if (n >= 1e4) return Math.round(n / 1e3) + "K";
    if (n >= 1e3) return (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K";
    return String(n);
  }

  function countUp(el, target, full) {
    var start = null, dur = 1200;
    function step(t) {
      if (!start) start = t;
      var p = Math.min((t - start) / dur, 1), eased = 1 - Math.pow(1 - p, 3);
      var v = Math.round(target * eased);
      el.textContent = full ? v.toLocaleString("en-US") : compact(v);
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function apply(stats) {
    var els = document.querySelectorAll("[data-stat]");
    els.forEach(function (el) {
      var key = el.getAttribute("data-stat"), full = el.getAttribute("data-format") === "full";
      var value = null;
      if (key === "total") value = stats.total_views;
      else if (stats.platforms && stats.platforms[key]) value = stats.platforms[key].views;
      else if (key === "window") { el.textContent = stats.window_days + " days"; return; }
      else if (key === "updated") {
        var d = new Date(stats.generated_at);
        el.textContent = "Updated " + d.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });
        return;
      }
      if (typeof value !== "number" || value <= 0) return; // keep fallback text

      // animate when the number scrolls into view
      if ("IntersectionObserver" in window) {
        var io = new IntersectionObserver(function (entries) {
          entries.forEach(function (e) {
            if (e.isIntersecting) { countUp(el, value, full); io.disconnect(); }
          });
        }, { threshold: 0.4 });
        io.observe(el);
      } else {
        el.textContent = full ? value.toLocaleString("en-US") : compact(value);
      }
    });
  }

  fetch(STATS_URL + "?v=" + new Date().toISOString().slice(0, 10), { cache: "no-store" })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(apply)
    .catch(function () { /* silently keep the hard-coded numbers */ });
})();
