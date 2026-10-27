/* Shared helpers: mobile navigation, a JSON fetch wrapper that sends the
   CSRF token and turns every failure into a readable Error message, and the
   best-fix location helper used by the report form and the map. */
(function () {
    "use strict";

    var toggle = document.getElementById("nav-toggle");
    var nav = document.getElementById("site-nav");
    if (toggle && nav) {
        toggle.addEventListener("click", function () {
            var open = nav.classList.toggle("open");
            toggle.setAttribute("aria-expanded", String(open));
            toggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
        });
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        return meta ? meta.content : "";
    }

    /* Returns parsed JSON, or throws Error(message) for network errors,
       non-JSON responses (e.g. a login redirect) and HTTP error statuses. */
    async function fetchJSON(url, options) {
        options = options || {};
        var headers = Object.assign({ "Accept": "application/json" }, options.headers || {});
        if (options.method && options.method !== "GET") {
            headers["X-CSRF-Token"] = csrfToken();
        }
        if (options.body !== undefined) {
            headers["Content-Type"] = "application/json";
        }
        var response;
        try {
            response = await fetch(url, Object.assign({}, options, {
                headers: headers,
                credentials: "same-origin",
                body: options.body !== undefined ? JSON.stringify(options.body) : undefined
            }));
        } catch (networkError) {
            throw new Error("Cannot reach the server. Check your connection and try again.");
        }
        var data = null;
        try { data = await response.json(); } catch (parseError) { /* handled below */ }
        if (!response.ok) {
            if (response.status === 401) {
                throw new Error("Your administrator session has expired. Please log in again.");
            }
            throw new Error((data && data.error) || "The request failed (HTTP " + response.status + ").");
        }
        if (data === null) {
            throw new Error("The server sent an unexpected response.");
        }
        return data;
    }

    /* Build an inline SVG string for a Lucide icon from icons.js. */
    function iconSvg(name, size) {
        var body = (window.TANAW_ICONS || {})[name] || "";
        size = size || 16;
        return '<svg xmlns="http://www.w3.org/2000/svg" width="' + size + '" height="' + size +
            '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
            'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + body + "</svg>";
    }

    /* ---------- Best-fix location ----------
       A single getCurrentPosition() call often returns the first, roughest
       reading (cell tower or Wi-Fi). Instead we listen for a few seconds and
       keep the most accurate reading. Used by the report form and the map.

       TANAW.locate({ onUpdate, onDone, onError }) returns a cancel function.
         onUpdate(best, info)  every time a more accurate reading arrives
         onDone(best, info)    once, with the best reading
         info = { accuracy, quality: "good"|"fair"|"poor", poor: boolean,
                  seconds, readings }
       Laptops have no GPS, so their accuracy is usually hundreds of metres or
       worse; the "poor" flag lets the page warn the person about that. */
    var LOCATE_DEFAULTS = {
        targetAccuracy: 10,   /* metres: stop early once a reading is this good */
        goodAccuracy: 30,     /* at or under this = "good" */
        poorAccuracy: 100,    /* over this = "poor", warn the person */
        window: 8000,         /* ms to keep listening for a better reading */
        timeout: 20000        /* ms to wait for the very first reading */
    };

    function qualityOf(accuracy, opts) {
        if (accuracy <= opts.goodAccuracy) { return "good"; }
        if (accuracy <= opts.poorAccuracy) { return "fair"; }
        return "poor";
    }

    function formatAccuracy(metres) {
        if (!isFinite(metres)) { return "unknown accuracy"; }
        return metres >= 1000 ? (metres / 1000).toFixed(1) + " km" : Math.round(metres) + " m";
    }

    function locate(options) {
        options = options || {};
        var opts = Object.assign({}, LOCATE_DEFAULTS, options);
        var geo = typeof navigator !== "undefined" ? navigator.geolocation : null;
        var noop = function () {};
        var onUpdate = options.onUpdate || noop;
        var onDone = options.onDone || noop;
        var onError = options.onError || noop;
        if (!geo) { onError({ code: 0, message: "unsupported" }); return noop; }

        var best = null, readings = 0, finished = false, watchId = null, windowTimer = null, hardTimer = null;
        var started = Date.now();

        function info() {
            var accuracy = best ? best.coords.accuracy : Infinity;
            return { accuracy: accuracy, quality: qualityOf(accuracy, opts), poor: accuracy > opts.poorAccuracy,
                     seconds: (Date.now() - started) / 1000, readings: readings };
        }
        function stop() {
            finished = true;
            if (watchId !== null && geo.clearWatch) { geo.clearWatch(watchId); }
            clearTimeout(windowTimer); clearTimeout(hardTimer);
        }
        function finish() {
            if (finished) { return; }
            stop();
            onDone(best, info());
        }
        function fail(err) {
            if (finished) { return; }
            /* A later error does not discard a reading we already have. */
            if (best) { finish(); return; }
            stop();
            onError(err);
        }
        function accept(position) {
            if (finished) { return; }
            readings += 1;
            var better = !best || position.coords.accuracy < best.coords.accuracy;
            if (better) { best = position; onUpdate(best, info()); }
            if (best.coords.accuracy <= opts.targetAccuracy) { finish(); return; }
            /* The listening window has passed and we now have a reading. */
            if (Date.now() - started >= opts.window) { finish(); }
        }

        var geoOptions = { enableHighAccuracy: true, maximumAge: 0, timeout: opts.timeout };

        if (typeof geo.watchPosition !== "function") {
            /* Very old browsers: a single reading is all we can get. */
            geo.getCurrentPosition(function (position) { accept(position); finish(); }, fail, geoOptions);
            return function () { stop(); };
        }

        watchId = geo.watchPosition(accept, fail, geoOptions);
        windowTimer = setTimeout(function () { if (best) { finish(); } }, opts.window);
        hardTimer = setTimeout(function () {
            if (best) { finish(); } else { fail({ code: 3, message: "timeout" }); }
        }, opts.timeout);
        return function cancel() { stop(); };
    }

    window.TANAW = { fetchJSON: fetchJSON, csrfToken: csrfToken, iconSvg: iconSvg,
                     locate: locate, formatAccuracy: formatAccuracy };
})();
