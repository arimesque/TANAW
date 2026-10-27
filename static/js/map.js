/* ==========================================================================
   TANAW public map
   - One L.Marker per saved marking, kept in `markerById` (no duplicates).
   - One layer group per category; filters only add/remove those groups.
   - Administrators add/edit/delete through the protected API; the server
     enforces permission, this file only hides controls for visitors.
   Server-provided values arrive through data-* attributes on #tanaw-map.
   ========================================================================== */
(function () {
    "use strict";

    var mapElement = document.getElementById("tanaw-map");
    if (!mapElement || typeof L === "undefined") { return; }

    var api = window.TANAW;
    var isAdmin = mapElement.dataset.isAdmin === "true";
    var BOUNDARY_URL = mapElement.dataset.boundaryUrl;

    /* ---------- Category visual system (colour + icon + label) ---------- */
    var CATEGORIES = {
        incident:          { label: "Incident",          plural: "Incidents",          color: "#c62828", icon: "triangle-alert", hint: "Reported or confirmed events" },
        facility:          { label: "Facility",          plural: "Facilities",         color: "#1565c0", icon: "building-2",     hint: "MDRRMO and other key locations" },
        evacuation_center: { label: "Evacuation center", plural: "Evacuation centers", color: "#2e7d32", icon: "house",          hint: "Designated shelters" },
        hazard:            { label: "Hazard",            plural: "Hazards",            color: "#b45309", icon: "octagon-alert",  hint: "Flood-prone or risky areas" },
        other:             { label: "Other",             plural: "Other locations",    color: "#546e7a", icon: "map-pin",        hint: "Other mapped places" }
    };

    /* Specific types, grouped under the categories above. Mirrors MARKING_TYPES
       in app.py; "temporary" types can be resolved or given an expiry date. */
    var TYPES = {
        flooded_road:       { category: "hazard",   label: "Flooded road",           icon: "waves",        temporary: true },
        road_construction:  { category: "hazard",   label: "Road construction",      icon: "construction", temporary: true },
        closed_road:        { category: "hazard",   label: "Closed road",            icon: "ban",          temporary: true },
        road_damage:        { category: "hazard",   label: "Potholes / road damage", icon: "circle-dot",   temporary: true },
        fallen_tree_debris: { category: "incident", label: "Fallen tree / debris",   icon: "tree-pine",    temporary: true },
        landslide:          { category: "incident", label: "Landslide",              icon: "mountain",     temporary: true },
        barangay_hall:      { category: "facility", label: "Barangay hall",          icon: "landmark",     temporary: false },
        health_center:      { category: "facility", label: "Health center",          icon: "hospital",     temporary: false },
        fire_station:       { category: "facility", label: "Fire station",           icon: "flame",        temporary: false }
    };

    var DEFAULT_VIEW = { center: [15.0, 120.652], zoom: 13 };

    /* ---------- Map setup ---------- */
    L.Icon.Default.imagePath = mapElement.dataset.markerImagePath;
    var map = L.map(mapElement, { zoomControl: true, minZoom: 10 }).setView(DEFAULT_VIEW.center, DEFAULT_VIEW.zoom);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);
    L.control.scale({ imperial: false }).addTo(map);

    /* Boundaries sit below markers so they never hide them. */
    map.createPane("barangays").style.zIndex = 350;

    var categoryLayers = {};
    Object.keys(CATEGORIES).forEach(function (key) {
        categoryLayers[key] = L.layerGroup().addTo(map);
    });
    var markerById = new Map();
    var showInactive = document.getElementById("show-inactive");

    /* ---------- Small helpers ---------- */
    var messageEl = document.getElementById("map-message");
    function say(text, isError) {
        if (!messageEl) { return; }
        messageEl.textContent = text;
        messageEl.classList.toggle("is-error", !!isError);
    }

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) { node.className = className; }
        if (text !== undefined) { node.textContent = text; }
        return node;
    }

    function validCoords(lat, lon) {
        return Number.isFinite(lat) && Number.isFinite(lon) && Math.abs(lat) <= 90 && Math.abs(lon) <= 180;
    }

    function formatDate(iso) {
        var d = new Date(iso);
        return isNaN(d) ? "" : d.toLocaleDateString("en-PH", { year: "numeric", month: "short", day: "numeric", timeZone: "Asia/Manila" });
    }

    function categoryOf(marking) {
        var key = String(marking.category || "").toLowerCase();
        return CATEGORIES[key] ? key : "other";
    }

    /* A known type of this marking in its category, else null. */
    function typeOf(marking) {
        var t = TYPES[String(marking.marking_type || "")];
        return t && t.category === categoryOf(marking) ? t : null;
    }

    /* Resolved or expired markings stay on record but are dimmed and hidden by default. */
    function isActive(marking) { return marking.is_active !== false; }

    function stateLabel(marking) {
        if (isActive(marking)) { return ""; }
        return marking.status === "resolved" ? "Resolved" : "Expired";
    }

    /* ---------- Markers ---------- */
    function markerIcon(categoryKey, extraClass, typeInfo) {
        var cat = CATEGORIES[categoryKey];
        if (typeInfo) { cat = { icon: typeInfo.icon }; }
        return L.divIcon({
            className: "tdivicon",
            html: '<div class="tmarker cat-' + categoryKey + (extraClass ? " " + extraClass : "") + '">' + api.iconSvg(cat.icon, 17) + "</div>",
            iconSize: [34, 34],
            iconAnchor: [17, 34],
            popupAnchor: [0, -32]
        });
    }

    function buildPopup(marking) {
        var key = categoryOf(marking);
        var cat = CATEGORIES[key];
        var box = el("div", "pop");

        var chip = el("span", "pop-cat");
        chip.style.background = cat.color;
        var typeInfo = typeOf(marking);
        chip.innerHTML = api.iconSvg(typeInfo ? typeInfo.icon : cat.icon, 13);
        chip.appendChild(document.createTextNode(typeInfo ? cat.label + " · " + typeInfo.label : cat.label));
        box.appendChild(chip);
        if (stateLabel(marking)) {
            var state = el("span", "pop-cat", stateLabel(marking));
            state.style.background = "#546e7a";
            state.style.marginLeft = "6px";
            box.appendChild(state);
        }

        box.appendChild(el("h3", "pop-title", marking.name || "Untitled marking"));

        if (marking.location) {
            var loc = el("p", "pop-line");
            loc.innerHTML = api.iconSvg("map-pin", 14);
            loc.appendChild(el("span", "", marking.location));
            box.appendChild(loc);
        }
        if (marking.description) {
            box.appendChild(el("p", "pop-line", marking.description));
        }
        if (marking.expires_at) { box.appendChild(el("p", "pop-meta", "Expires " + formatDate(marking.expires_at + "T00:00:00+08:00"))); }
        var updated = formatDate(marking.updated_at);
        if (updated) { box.appendChild(el("p", "pop-meta", "Updated " + updated)); }

        /* Directions, stage 1: hand the destination to an external navigation app. */
        var dest = Number(marking.latitude) + "," + Number(marking.longitude);
        var links = el("div", "pop-actions");
        [["Open in Google Maps", "https://www.google.com/maps/dir/?api=1&destination=" + dest, "map"],
         ["Open in Waze", "https://waze.com/ul?ll=" + dest + "&navigate=yes", "navigation"]].forEach(function (def) {
            var a = el("a", "btn btn-secondary btn-sm");
            a.href = def[1];
            a.target = "_blank";
            a.rel = "noopener noreferrer";
            a.innerHTML = api.iconSvg(def[2], 14);
            a.appendChild(document.createTextNode(" " + def[0]));
            links.appendChild(a);
        });
        box.appendChild(links);

        if (isAdmin) {
            var actions = el("div", "pop-actions");
            var edit = el("button", "btn btn-secondary btn-sm");
            edit.type = "button";
            edit.innerHTML = api.iconSvg("pencil", 14);
            edit.appendChild(document.createTextNode(" Edit"));
            edit.addEventListener("click", function () { map.closePopup(); startEdit(marking); });

            var del = el("button", "btn btn-danger btn-sm");
            del.type = "button";
            del.innerHTML = api.iconSvg("trash-2", 14);
            del.appendChild(document.createTextNode(" Delete"));
            del.addEventListener("click", function () { deleteMarking(marking); });

            actions.appendChild(edit);
            actions.appendChild(del);
            box.appendChild(actions);
        }
        return box;
    }

    /* Insert or replace the marker for a record. Returns false if unusable. */
    function upsertMarker(marking) {
        var lat = Number(marking.latitude);
        var lon = Number(marking.longitude);
        if (marking.latitude === null || marking.longitude === null || !validCoords(lat, lon)) {
            console.warn("Skipping marking with invalid coordinates:", marking.id);
            return false;
        }
        removeMarker(marking.id);

        var key = categoryOf(marking);
        var typeInfo = typeOf(marking);
        var what = typeInfo ? typeInfo.label : CATEGORIES[key].label;
        var marker = L.marker([lat, lon], {
            icon: markerIcon(key, null, typeInfo),
            title: (marking.name || "Marking") + " (" + what + (stateLabel(marking) ? ", " + stateLabel(marking).toLowerCase() : "") + ")",
            alt: (marking.name || "Marking") + ", " + what,
            opacity: isActive(marking) ? 1 : 0.5,
            riseOnHover: true
        });
        marker.bindPopup(buildPopup(marking), { maxWidth: 280 });
        if (isActive(marking) || (showInactive && showInactive.checked)) { marker.addTo(categoryLayers[key]); }
        markerById.set(marking.id, { marker: marker, marking: marking, category: key });
        return true;
    }

    function removeMarker(id) {
        var entry = markerById.get(id);
        if (entry) {
            categoryLayers[entry.category].removeLayer(entry.marker);
            markerById.delete(id);
        }
    }

    function updateCounts() {
        var counts = {};
        markerById.forEach(function (entry) {
            if (isActive(entry.marking)) { counts[entry.category] = (counts[entry.category] || 0) + 1; }
        });
        document.querySelectorAll("[data-count]").forEach(function (node) {
            node.textContent = counts[node.dataset.count] || 0;
        });
    }

    /* ---------- Filters: visibility only; markers always exist ---------- */
    function applyFilters() {
        document.querySelectorAll(".category-filter").forEach(function (box) {
            var layer = categoryLayers[box.value];
            if (!layer) { return; }
            if (box.checked && !map.hasLayer(layer)) { map.addLayer(layer); }
            if (!box.checked && map.hasLayer(layer)) { map.removeLayer(layer); }
        });
    }
    document.querySelectorAll(".category-filter").forEach(function (box) {
        box.addEventListener("change", applyFilters);
    });

    function applyInactiveFilter() {
        markerById.forEach(function (entry) {
            var layer = categoryLayers[entry.category];
            var visible = isActive(entry.marking) || showInactive.checked;
            if (visible && !layer.hasLayer(entry.marker)) { entry.marker.addTo(layer); }
            if (!visible && layer.hasLayer(entry.marker)) { layer.removeLayer(entry.marker); }
        });
    }
    if (showInactive) { showInactive.addEventListener("change", applyInactiveFilter); }

    function ensureCategoryVisible(key) {
        var box = document.querySelector('.category-filter[value="' + key + '"]');
        if (box && !box.checked) { box.checked = true; applyFilters(); }
    }

    /* ---------- Legend ---------- */
    (function buildLegend() {
        var list = document.getElementById("legend-list");
        if (!list) { return; }
        Object.keys(CATEGORIES).forEach(function (key) {
            var cat = CATEGORIES[key];
            var li = el("li");
            var swatch = el("span", "swatch cat-" + key);
            swatch.innerHTML = api.iconSvg(cat.icon, 14);
            var text = el("span");
            text.appendChild(el("strong", "", cat.label));
            text.appendChild(el("small", "", cat.hint));
            li.appendChild(swatch);
            li.appendChild(text);
            list.appendChild(li);
        });

        var typeList = document.getElementById("legend-types");
        if (!typeList) { return; }
        Object.keys(TYPES).forEach(function (key) {
            var type = TYPES[key];
            var li = el("li");
            var swatch = el("span", "swatch cat-" + type.category);
            swatch.innerHTML = api.iconSvg(type.icon, 14);
            var text = el("span");
            text.appendChild(el("strong", "", type.label));
            text.appendChild(el("small", "", CATEGORIES[type.category].label + (type.temporary ? " · temporary" : "")));
            li.appendChild(swatch);
            li.appendChild(text);
            typeList.appendChild(li);
        });
    })();

    /* ---------- Loading saved markings ---------- */
    var loadingEl = document.getElementById("map-loading");

    async function loadMarkings() {
        try {
            var data = await api.fetchJSON("/api/map-markings");
            if (!Array.isArray(data.markings)) { throw new Error("The server returned an invalid list of markings."); }

            var seen = new Set();
            data.markings.forEach(function (m) { if (upsertMarker(m)) { seen.add(m.id); } });
            Array.from(markerById.keys()).forEach(function (id) { if (!seen.has(id)) { removeMarker(id); } });

            applyFilters();
            updateCounts();
            say(seen.size === 0 ? "No map markings have been added yet." : seen.size + " map marking(s) loaded.");
        } catch (error) {
            console.error("Marking load error:", error);
            say("Could not load map markings. " + error.message, true);
        } finally {
            if (loadingEl) { loadingEl.hidden = true; }
        }
    }

    /* ---------- Barangay boundaries ---------- */
    var boundaryBounds = null;
    var boundaryStyle = { color: "#1f4e79", weight: 1.5, opacity: 0.85, dashArray: "5 4", fillColor: "#5d93c0", fillOpacity: 0.04 };
    var boundaryHover = { weight: 2.5, fillOpacity: 0.18, dashArray: null };

    var barangayLayer = L.geoJSON(null, {
        pane: "barangays",
        smoothFactor: 0, /* never alter the real geometry */
        style: function () { return boundaryStyle; },
        onEachFeature: function (feature, layer) {
            var name = (feature.properties && feature.properties.ADM4_EN) || "Unknown barangay";
            layer.bindTooltip(name, { sticky: true, className: "barangay-label" });
            layer.on("mouseover", function () { layer.setStyle(boundaryHover); });
            layer.on("mouseout", function () { barangayLayer.resetStyle(layer); });
            layer.on("click", function (event) {
                if (picking) { return; } /* let the click place a marking instead */
                var box = el("div", "pop");
                box.appendChild(el("h3", "pop-title", name));
                box.appendChild(el("p", "pop-meta", "Barangay, Bacolor, Pampanga"));
                L.popup().setLatLng(event.latlng).setContent(box).openOn(map);
            });
        }
    });

    var BOUNDARY_ATTRIBUTION = "Boundaries: PSA / NAMRIA (generalised)";
    var showBarangays = document.getElementById("show-barangays");

    var boundariesReady = fetch(BOUNDARY_URL, { headers: { "Accept": "application/json" } })
        .then(function (r) { if (!r.ok) { throw new Error("HTTP " + r.status); } return r.json(); })
        .then(function (geojson) {
            barangayLayer.addData(geojson);
            boundaryBounds = barangayLayer.getBounds();
            if (boundaryBounds.isValid() && !focusRequested) { map.fitBounds(boundaryBounds, { padding: [20, 20] }); }
            return true;
        })
        .catch(function (error) {
            console.error("Boundary load error:", error);
            if (showBarangays) { showBarangays.disabled = true; }
            var note = document.getElementById("boundary-note");
            if (note) { note.textContent = "Barangay boundaries could not be loaded."; }
            return false;
        });

    if (showBarangays) {
        showBarangays.addEventListener("change", async function () {
            if (showBarangays.checked) {
                if (!(await boundariesReady)) { showBarangays.checked = false; return; }
                map.addLayer(barangayLayer);
                map.attributionControl.addAttribution(BOUNDARY_ATTRIBUTION);
                say("Barangay boundaries shown. Click a barangay to see its name.");
            } else {
                map.removeLayer(barangayLayer);
                map.attributionControl.removeAttribution(BOUNDARY_ATTRIBUTION);
                say("Barangay boundaries hidden.");
            }
        });
    }

    /* ---------- Map controls: reset view, my location ---------- */
    function resetView() {
        if (boundaryBounds && boundaryBounds.isValid()) {
            map.fitBounds(boundaryBounds, { padding: [20, 20] });
        } else {
            map.setView(DEFAULT_VIEW.center, DEFAULT_VIEW.zoom);
        }
        say("Map returned to the Bacolor overview.");
    }

    var locationLayer = L.layerGroup().addTo(map);
    function locateMe() {
        if (!navigator.geolocation) { say("Your browser does not support location access.", true); return; }
        say("Requesting your location. Please allow access if prompted.");
        navigator.geolocation.getCurrentPosition(function (position) {
            var here = [position.coords.latitude, position.coords.longitude];
            locationLayer.clearLayers();
            L.circle(here, { radius: position.coords.accuracy, color: "#164b78", weight: 1, fillOpacity: 0.08 }).addTo(locationLayer);
            L.circleMarker(here, { radius: 8, color: "#fff", weight: 2, fillColor: "#164b78", fillOpacity: 1 })
                .bindPopup("Your current location (accurate to about " + Math.round(position.coords.accuracy) + " m).")
                .addTo(locationLayer);
            map.setView(here, 16);
            say("Your current location is shown. It is not saved or sent anywhere.");
        }, function (error) {
            var messages = {
                1: "Location access was denied. Allow it in your browser settings to use this button.",
                2: "Your location is currently unavailable.",
                3: "The location request timed out. Please try again."
            };
            say(messages[error.code] || "Could not get your location.", true);
        }, { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
    }

    var ActionControl = L.Control.extend({
        options: { position: "topright" },
        onAdd: function () {
            var bar = L.DomUtil.create("div", "leaflet-bar tcontrol");
            [["locate-fixed", "Show my location", locateMe], ["maximize", "Reset to Bacolor overview", resetView]].forEach(function (def) {
                var b = L.DomUtil.create("button", "", bar);
                b.type = "button";
                b.title = def[1];
                b.setAttribute("aria-label", def[1]);
                b.innerHTML = api.iconSvg(def[0], 18);
                L.DomEvent.on(b, "click", function (e) { L.DomEvent.stop(e); def[2](); });
            });
            L.DomEvent.disableClickPropagation(bar);
            return bar;
        }
    });
    new ActionControl().addTo(map);

    /* ---------- Search: saved markings (local) + Nominatim (optional) ---------- */
    var searchForm = document.getElementById("place-search-form");
    var searchInput = document.getElementById("place-search");
    var resultsEl = document.getElementById("search-results");
    var searchPin = null;

    function clearResults() { resultsEl.hidden = true; resultsEl.innerHTML = ""; }

    function addResult(label, iconName, onPick) {
        var li = el("li");
        var button = el("button");
        button.type = "button";
        button.innerHTML = api.iconSvg(iconName, 16);
        button.appendChild(el("span", "", label));
        button.addEventListener("click", function () { clearResults(); onPick(); });
        li.appendChild(button);
        resultsEl.appendChild(li);
    }

    function addNote(text) {
        var li = el("li", "result-note", text);
        resultsEl.appendChild(li);
    }

    function showSearchPin(lat, lon, label) {
        if (searchPin) { map.removeLayer(searchPin); }
        searchPin = L.circleMarker([lat, lon], { radius: 9, color: "#fff", weight: 3, fillColor: "#6a1b9a", fillOpacity: 1 })
            .bindPopup(el("div", "pop-line", label)).addTo(map);
        map.setView([lat, lon], 17);
        searchPin.openPopup();
    }

    if (searchForm) {
        searchForm.addEventListener("submit", async function (event) {
            event.preventDefault();
            var query = searchInput.value.trim();
            if (!query) { return; }
            resultsEl.innerHTML = "";
            resultsEl.hidden = false;

            /* 1. Saved markings: works offline from the loaded data. */
            var needle = query.toLowerCase();
            var local = [];
            markerById.forEach(function (entry) {
                var m = entry.marking;
                if ((m.name || "").toLowerCase().indexOf(needle) !== -1 || (m.location || "").toLowerCase().indexOf(needle) !== -1) { local.push(entry); }
            });
            local.slice(0, 5).forEach(function (entry) {
                var foundType = typeOf(entry.marking);
                addResult(entry.marking.name + " · " + (foundType ? foundType.label : CATEGORIES[entry.category].label), foundType ? foundType.icon : CATEGORIES[entry.category].icon, function () {
                    ensureCategoryVisible(entry.category);
                    if (!isActive(entry.marking) && showInactive && !showInactive.checked) { showInactive.checked = true; applyInactiveFilter(); }
                    map.setView(entry.marker.getLatLng(), 17);
                    entry.marker.openPopup();
                });
            });

            /* 2. Places and addresses from OpenStreetMap Nominatim. */
            addNote("Searching places…");
            var note = resultsEl.lastChild;
            var controller = new AbortController();
            var timer = setTimeout(function () { controller.abort(); }, 8000);
            try {
                var params = new URLSearchParams({
                    q: query, format: "jsonv2", limit: "5", countrycodes: "ph",
                    viewbox: "120.56,15.12,120.74,14.94", "accept-language": "en"
                });
                var response = await fetch("https://nominatim.openstreetmap.org/search?" + params, {
                    headers: { "Accept": "application/json" }, signal: controller.signal
                });
                if (!response.ok) { throw new Error("HTTP " + response.status); }
                var results = await response.json();
                resultsEl.removeChild(note);
                var usable = (Array.isArray(results) ? results : []).filter(function (r) { return validCoords(Number(r.lat), Number(r.lon)); });
                usable.forEach(function (r) {
                    addResult(r.display_name, "map-pin", function () { showSearchPin(Number(r.lat), Number(r.lon), r.display_name); });
                });
                if (!usable.length && !local.length) { addNote("No matching place was found."); }
            } catch (error) {
                console.warn("Place search unavailable:", error);
                note.textContent = local.length
                    ? "Place search is unavailable right now; showing saved markings only."
                    : "Place search is unavailable right now. Check your connection and try again.";
            } finally {
                clearTimeout(timer);
            }
        });

        searchInput.addEventListener("input", function () { if (!searchInput.value) { clearResults(); } });
        document.addEventListener("keydown", function (e) { if (e.key === "Escape") { clearResults(); } });
    }

    /* ---------- Administrator: add / edit / delete ---------- */
    var picking = false;       /* next map click chooses the location */
    var editingId = null;      /* null while adding a new marking */
    var coords = null;         /* [lat, lon] currently chosen */
    var coordsChanged = false; /* edits keep the saved location unless this is true */
    var selectionMarker = null;

    var form = document.getElementById("marking-form");
    var addButton = document.getElementById("add-marking");
    var pickButton = document.getElementById("pick-location");
    var cancelButton = document.getElementById("cancel-marking");
    var saveButton = document.getElementById("save-marking");
    var coordText = document.getElementById("selected-coordinates");
    var coordBox = document.getElementById("coord-box");
    var hint = document.getElementById("marking-hint");

    function field(id) { return document.getElementById(id); }

    function setFieldError(name, message) {
        var node = form.querySelector('[data-error-for="' + name + '"]');
        if (!node) { return; }
        node.textContent = message || "";
        node.hidden = !message;
        if (name === "name") { field("marking-name").classList.toggle("is-invalid", !!message); }
    }

    function showCoords() {
        if (!coords) {
            coordText.textContent = "Not selected yet. Click the map or press “Pick location on map”.";
            coordBox.classList.add("is-missing");
        } else {
            coordText.textContent = coords[0].toFixed(6) + ", " + coords[1].toFixed(6);
            coordBox.classList.remove("is-missing");
        }
    }

    function setPicking(on) {
        picking = on;
        mapElement.classList.toggle("picking", on);
        if (pickButton) { pickButton.setAttribute("aria-pressed", String(on)); }
        if (on) { map.closePopup(); say("Click the map to choose the location, or drag the blue pin."); }
    }

    function placeSelection(lat, lon, changed) {
        coords = [lat, lon];
        if (changed) { coordsChanged = true; }
        if (!selectionMarker) {
            selectionMarker = L.marker(coords, {
                draggable: true, zIndexOffset: 1000, title: "Selected location (drag to adjust)",
                icon: L.divIcon({ className: "tdivicon", html: '<div class="tmarker selecting">' + api.iconSvg("plus", 17) + "</div>", iconSize: [34, 34], iconAnchor: [17, 34] })
            }).addTo(map);
            selectionMarker.on("dragend", function () {
                var p = selectionMarker.getLatLng();
                coords = [p.lat, p.lng];
                coordsChanged = true;
                setFieldError("location", "");
                showCoords();
            });
        } else {
            selectionMarker.setLatLng(coords);
        }
        setFieldError("location", "");
        showCoords();
    }

    /* Type list follows the chosen category; status/expiry only for temporary types. */
    function fillTypes(selected) {
        var select = field("marking-type");
        var category = field("marking-category").value;
        select.innerHTML = "";
        var none = el("option", "", "General (no specific type)");
        none.value = "";
        select.appendChild(none);
        Object.keys(TYPES).forEach(function (key) {
            if (TYPES[key].category !== category) { return; }
            var option = el("option", "", TYPES[key].label);
            option.value = key;
            select.appendChild(option);
        });
        select.value = selected && TYPES[selected] && TYPES[selected].category === category ? selected : "";
        select.disabled = select.options.length < 2;
        toggleStatusFields();
    }

    function toggleStatusFields() {
        var temporary = !!(TYPES[field("marking-type").value] && TYPES[field("marking-type").value].temporary);
        field("marking-status-fields").hidden = !temporary;
        if (!temporary) { field("marking-status").value = "active"; field("marking-expires").value = ""; }
    }

    function closeForm() {
        setPicking(false);
        if (selectionMarker) { map.removeLayer(selectionMarker); selectionMarker = null; }
        form.hidden = true;
        form.reset();
        setFieldError("name", ""); setFieldError("location", "");
        editingId = null; coords = null; coordsChanged = false;
        addButton.disabled = false;
        hint.hidden = false;
    }

    function openForm(title) {
        form.hidden = false;
        addButton.disabled = true;
        hint.hidden = true;
        field("marking-form-title").textContent = title;
        setFieldError("name", ""); setFieldError("location", "");
        if (typeof form.scrollIntoView === "function") { form.scrollIntoView({ block: "nearest" }); }
        field("marking-name").focus();
    }

    function startAdd(prefill) {
        if (!isAdmin || !form) { return; }
        closeForm();
        prefill = prefill || {};
        editingId = null;
        openForm("Add map marking");
        field("marking-name").value = prefill.name || "";
        field("marking-category").value = CATEGORIES[prefill.category] ? prefill.category : "incident";
        fillTypes("");
        field("marking-location").value = prefill.location || "";
        field("marking-description").value = prefill.description || "";
        if (prefill.coords) {
            placeSelection(prefill.coords[0], prefill.coords[1], true);
            map.setView(prefill.coords, 17);
            say("Check the pin, complete the details, and save.");
        } else {
            showCoords();
            setPicking(true);
        }
    }

    function startEdit(marking) {
        if (!isAdmin || !form) { return; }
        closeForm();
        editingId = marking.id;
        openForm("Edit map marking");
        field("marking-name").value = marking.name || "";
        field("marking-category").value = categoryOf(marking);
        fillTypes(marking.marking_type);
        field("marking-status").value = marking.status === "resolved" ? "resolved" : "active";
        field("marking-expires").value = marking.expires_at || "";
        field("marking-location").value = marking.location || "";
        field("marking-description").value = marking.description || "";
        placeSelection(Number(marking.latitude), Number(marking.longitude), false);
        say("Editing “" + marking.name + "”. Its location stays the same unless you move the blue pin.");
    }

    async function saveMarking(event) {
        event.preventDefault();
        var name = field("marking-name").value.trim();
        setFieldError("name", name ? "" : "Please enter a name.");
        var editing = editingId !== null;
        setFieldError("location", (!editing && !coords) ? "Please choose a location on the map." : "");
        if (!name || (!editing && !coords)) { say("Please fix the highlighted fields.", true); return; }

        var payload = {
            name: name,
            category: field("marking-category").value,
            marking_type: field("marking-type").value,
            status: field("marking-status").value,
            expires_at: field("marking-expires").value,
            location: field("marking-location").value.trim(),
            description: field("marking-description").value.trim()
        };
        /* Only send coordinates when adding, or when the pin was deliberately moved. */
        if (!editing || coordsChanged) { payload.latitude = coords[0]; payload.longitude = coords[1]; }

        saveButton.disabled = true;
        saveButton.lastChild.textContent = " Saving…";
        try {
            var result = await api.fetchJSON(editing ? "/api/map-markings/" + editingId : "/api/map-markings", {
                method: editing ? "PUT" : "POST", body: payload
            });
            var saved = result.marking;
            if (!saved || !upsertMarker(saved)) { throw new Error("The server returned an invalid marking."); }
            ensureCategoryVisible(categoryOf(saved));
            if (!isActive(saved) && showInactive && !showInactive.checked) { showInactive.checked = true; applyInactiveFilter(); }
            updateCounts();
            closeForm();
            map.setView([saved.latitude, saved.longitude], Math.max(map.getZoom(), 16));
            markerById.get(saved.id).marker.openPopup();
            say(editing ? "Map marking updated." : "Map marking saved.");
        } catch (error) {
            console.error("Marking save error:", error);
            say(error.message, true);
        } finally {
            saveButton.disabled = false;
            saveButton.lastChild.textContent = " Save";
        }
    }

    async function deleteMarking(marking) {
        if (!window.confirm("Delete “" + marking.name + "”? This cannot be undone.")) { return; }
        try {
            await api.fetchJSON("/api/map-markings/" + marking.id, { method: "DELETE" });
            removeMarker(marking.id);
            updateCounts();
            map.closePopup();
            if (editingId === marking.id) { closeForm(); }
            say("Map marking deleted.");
        } catch (error) {
            console.error("Marking delete error:", error);
            say(error.message, true);
        }
    }

    if (isAdmin && form) {
        addButton.addEventListener("click", function () { startAdd(); });
        pickButton.addEventListener("click", function () { setPicking(!picking); });
        cancelButton.addEventListener("click", function () { closeForm(); say("Changes cancelled."); });
        form.addEventListener("submit", saveMarking);
        form.addEventListener("keydown", function (e) { if (e.key === "Escape") { closeForm(); say("Changes cancelled."); } });
        field("marking-name").addEventListener("input", function () { setFieldError("name", ""); });
        field("marking-category").addEventListener("change", function () { fillTypes(""); });
        field("marking-type").addEventListener("change", toggleStatusFields);

        map.on("click", function (event) {
            if (!picking) { return; }
            placeSelection(event.latlng.lat, event.latlng.lng, true);
            setPicking(false);
            say("Location selected. Drag the pin to fine-tune it, then save.");
        });
    }

    /* ---------- URL parameters: ?focus=lat,lng and admin ?add=1&lat=&lng=… ---------- */
    var params = new URLSearchParams(window.location.search);
    var focusRequested = false;
    (function readParams() {
        var focus = (params.get("focus") || "").split(",").map(Number);
        var lat = Number(params.get("lat"));
        var lon = Number(params.get("lng"));
        if (focus.length === 2 && validCoords(focus[0], focus[1])) {
            focusRequested = true;
            map.setView(focus, 17);
            L.circleMarker(focus, { radius: 14, color: "#c62828", weight: 3, fillOpacity: 0.1 }).addTo(map)
                .bindPopup(el("div", "pop-line", "Reported location")).openPopup();
        }
        if (isAdmin && params.get("add") === "1") {
            var prefillCoords = validCoords(lat, lon) && params.get("lat") !== null ? [lat, lon] : null;
            focusRequested = focusRequested || !!prefillCoords;
            startAdd({
                name: params.get("name") || "", category: params.get("category") || "incident",
                location: params.get("location") || "", description: params.get("description") || "",
                coords: prefillCoords
            });
        }
    })();

    /* Keep tiles correct when panels open/close or the window resizes. */
    if (window.ResizeObserver) { new ResizeObserver(function () { map.invalidateSize(); }).observe(mapElement); }

    /* Panel sections default to open on desktop, collapsed on phones. */
    if (window.matchMedia("(max-width: 860px)").matches) {
        document.querySelectorAll(".panel-section").forEach(function (d) {
            if (d.id === "legend-section") { d.open = false; }
        });
    }

    loadMarkings();
})();
