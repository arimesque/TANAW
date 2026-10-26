/* Shared helpers: mobile navigation and a JSON fetch wrapper that sends the
   CSRF token and turns every failure into a readable Error message. */
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

    window.TANAW = { fetchJSON: fetchJSON, csrfToken: csrfToken, iconSvg: iconSvg };
})();
