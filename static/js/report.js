/* Public incident report form: location pin, photo preview, time handling and
   duplicate-submit protection. Location is requested only when the reporter
   presses the button, never automatically. */
(function () {
    "use strict";

    var form = document.getElementById("incident-form");
    var mapElement = document.getElementById("report-map");
    if (!form || !mapElement || typeof L === "undefined") { return; }

    var api = window.TANAW;
    var status = document.getElementById("location-status");
    var latInput = document.getElementById("latitude");
    var lonInput = document.getElementById("longitude");
    var BACOLOR = [15.0, 120.652];

    var map = L.map(mapElement).setView(BACOLOR, 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    var marker = null;
    var accuracyCircle = null;

    function pinIcon() {
        return L.divIcon({
            className: "tdivicon", iconSize: [34, 34], iconAnchor: [17, 34],
            html: '<div class="tmarker">' + api.iconSvg("triangle-alert", 17) + "</div>"
        });
    }

    function setPin(lat, lon, zoom, message) {
        latInput.value = lat.toFixed(6);
        lonInput.value = lon.toFixed(6);
        if (marker) {
            marker.setLatLng([lat, lon]);
        } else {
            marker = L.marker([lat, lon], { draggable: true, icon: pinIcon(), title: "Incident location (drag to adjust)" }).addTo(map);
            marker.on("dragend", function () {
                var p = marker.getLatLng();
                latInput.value = p.lat.toFixed(6);
                lonInput.value = p.lng.toFixed(6);
                status.textContent = "Pin moved. Check that it matches the incident location.";
            });
        }
        if (zoom) { map.setView([lat, lon], zoom); }
        status.textContent = message || "Pin placed. Check that it matches the incident location.";
    }

    map.on("click", function (e) { setPin(e.latlng.lat, e.latlng.lng, null, "Pin placed on the map. Drag it to adjust."); });

    /* Restore a pin after a validation error. */
    var savedLat = parseFloat(latInput.value);
    var savedLon = parseFloat(lonInput.value);
    if (isFinite(savedLat) && isFinite(savedLon)) { setPin(savedLat, savedLon, 16, "Your previous pin was kept. Adjust it if needed."); }

    document.getElementById("locate-button").addEventListener("click", function () {
        if (!navigator.geolocation) { status.textContent = "This browser cannot share your location. Tap the map instead."; return; }
        status.textContent = "Waiting for location permission…";
        navigator.geolocation.getCurrentPosition(function (pos) {
            setPin(pos.coords.latitude, pos.coords.longitude, 17,
                "Your current location was found (about " + Math.round(pos.coords.accuracy) + " m accuracy). Move the pin if the incident is elsewhere.");
            if (accuracyCircle) { map.removeLayer(accuracyCircle); }
            accuracyCircle = L.circle([pos.coords.latitude, pos.coords.longitude], { radius: pos.coords.accuracy, color: "#164b78", weight: 1, fillOpacity: 0.08 }).addTo(map);
        }, function (err) {
            var messages = {
                1: "Location permission was denied. You can still tap the map to choose a location.",
                2: "Your location could not be determined. Try again or tap the map.",
                3: "The location request timed out. Try again or tap the map."
            };
            status.textContent = messages[err.code] || "Unable to get your location. Tap the map instead.";
        }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 });
    });

    /* ---------- Photo selection and preview ---------- */
    var galleryInput = document.getElementById("photo-file");
    var cameraInput = document.getElementById("camera-file");
    var previewBox = document.getElementById("photo-preview");
    var previewImage = document.getElementById("preview-image");
    var previewUrl = null;

    function clearPhoto() {
        galleryInput.value = "";
        cameraInput.value = "";
        previewImage.removeAttribute("src");
        previewBox.hidden = true;
        if (previewUrl) { URL.revokeObjectURL(previewUrl); previewUrl = null; }
    }

    function onPhoto(event) {
        var input = event.target;
        var file = input.files && input.files[0];
        if (!file) { return; }
        if (!/^image\/(jpeg|png|webp)$/.test(file.type) && !/\.(jpe?g|png|webp)$/i.test(file.name)) {
            clearPhoto();
            alert("Please choose a JPG, PNG or WEBP image.");
            return;
        }
        if (file.size > 10 * 1024 * 1024) { clearPhoto(); alert("That photo is larger than 10 MB."); return; }
        /* Only one photo is kept, so the other input is cleared. */
        (input === galleryInput ? cameraInput : galleryInput).value = "";
        if (previewUrl) { URL.revokeObjectURL(previewUrl); }
        previewUrl = URL.createObjectURL(file);
        previewImage.src = previewUrl;
        previewBox.hidden = false;
    }
    galleryInput.addEventListener("change", onPhoto);
    cameraInput.addEventListener("change", onPhoto);
    document.getElementById("remove-photo").addEventListener("click", clearPhoto);

    /* ---------- Incident time ---------- */
    var timeInput = document.getElementById("incident-time");
    var unknown = document.getElementById("time-unknown");

    function localNowString() {
        var now = new Date();
        now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
        return now.toISOString().slice(0, 16);
    }
    timeInput.max = localNowString();

    function syncTime() {
        timeInput.disabled = unknown.checked;
        timeInput.required = !unknown.checked;
        if (unknown.checked) { timeInput.value = ""; }
    }
    unknown.addEventListener("change", syncTime);
    syncTime();

    /* ---------- Description counter ---------- */
    var description = document.getElementById("description");
    var counter = document.getElementById("desc-count");
    function updateCount() { counter.textContent = description.value.length.toLocaleString(); }
    description.addEventListener("input", updateCount);
    updateCount();

    /* ---------- Validation and duplicate-submit protection ---------- */
    var submitButton = document.getElementById("submit-button");
    var submitted = false;

    form.addEventListener("submit", function (event) {
        if (submitted) { event.preventDefault(); return; }
        if (!form.checkValidity()) {
            event.preventDefault();
            form.reportValidity();
            return;
        }
        submitted = true;
        submitButton.disabled = true;
        submitButton.querySelector("span").textContent = "Submitting…";
    });
    /* Re-enable if the user navigates back to a cached copy of the form. */
    window.addEventListener("pageshow", function (e) {
        if (e.persisted) { submitted = false; submitButton.disabled = false; submitButton.querySelector("span").textContent = "Submit report"; }
    });
})();
