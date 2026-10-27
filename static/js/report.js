/* Public incident report form: location pin, live camera and photo preview,
   time handling and duplicate-submit protection. Location and camera are
   requested only when the reporter presses the button, never automatically. */
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
    /* OpenStreetMap has real tiles only up to zoom 19. Zooming further just
       enlarges the zoom-19 picture, which still helps when placing a pin. */
    var NATIVE_MAX_ZOOM = 19, MAX_ZOOM = 21;

    var map = L.map(mapElement, { maxZoom: MAX_ZOOM }).setView(BACOLOR, 14);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxNativeZoom: NATIVE_MAX_ZOOM, maxZoom: MAX_ZOOM, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    var marker = null;
    var accuracyCircle = null;
    var cancelLocate = null;   /* set while "Use my current location" is listening */
    var locateButton = document.getElementById("locate-button");

    function setLocateBusy(busy) {
        locateButton.disabled = busy;
        locateButton.setAttribute("aria-busy", String(busy));
    }
    function setWarning(on) { status.classList.toggle("is-warning", !!on); }
    /* If the reporter places or drags the pin by hand, a later GPS reading
       must not move it back. */
    function stopLocating() {
        if (cancelLocate) { cancelLocate(); cancelLocate = null; setLocateBusy(false); }
    }

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
                stopLocating();
                setWarning(false);
                status.textContent = "Pin moved. Check that it matches the incident location.";
            });
        }
        if (zoom) { map.setView([lat, lon], zoom); }
        status.textContent = message || "Pin placed. Check that it matches the incident location.";
    }

    map.on("click", function (e) { stopLocating(); setWarning(false); setPin(e.latlng.lat, e.latlng.lng, null, "Pin placed on the map. Drag it to adjust."); });

    /* Restore a pin after a validation error. */
    var savedLat = parseFloat(latInput.value);
    var savedLon = parseFloat(lonInput.value);
    if (isFinite(savedLat) && isFinite(savedLon)) { setPin(savedLat, savedLon, 16, "Your previous pin was kept. Adjust it if needed."); }

    /* ---------- Accurate location ---------- */
    function showAccuracy(position) {
        var c = position.coords;
        if (accuracyCircle) { map.removeLayer(accuracyCircle); }
        accuracyCircle = L.circle([c.latitude, c.longitude], { radius: c.accuracy, color: "#164b78", weight: 1, fillOpacity: 0.08 }).addTo(map);
    }

    locateButton.addEventListener("click", function () {
        if (!navigator.geolocation) { status.textContent = "This browser cannot share your location. Tap the map instead."; return; }
        stopLocating();
        setWarning(false);
        setLocateBusy(true);
        status.textContent = "Waiting for location permission…";
        var firstReading = true;

        cancelLocate = api.locate({
            onUpdate: function (pos, info) {
                var c = pos.coords;
                /* Move the pin as better readings arrive, but recentre only once. */
                setPin(c.latitude, c.longitude, firstReading ? 17 : null,
                    "Improving accuracy… about " + api.formatAccuracy(info.accuracy) + " so far.");
                firstReading = false;
                showAccuracy(pos);
            },
            onDone: function (pos, info) {
                cancelLocate = null;
                setLocateBusy(false);
                var c = pos.coords;
                setPin(c.latitude, c.longitude, null, "");
                showAccuracy(pos);
                var text;
                if (info.poor) {
                    text = "Warning: this location is only accurate to about " + api.formatAccuracy(info.accuracy) +
                        ", which is too rough to trust. Laptops and indoor spots are often this inaccurate. " +
                        "Zoom in and drag the pin to the exact spot, or try again outdoors on a phone.";
                    setWarning(true);
                } else if (info.quality === "fair") {
                    text = "Location found (about " + api.formatAccuracy(info.accuracy) + " accuracy). " +
                        "Check the pin and drag it if the incident is elsewhere.";
                } else {
                    text = "Your current location was found (about " + api.formatAccuracy(info.accuracy) +
                        " accuracy). Move the pin if the incident is elsewhere.";
                }
                status.textContent = text;
            },
            onError: function (err) {
                cancelLocate = null;
                setLocateBusy(false);
                var messages = {
                    1: "Location permission was denied. You can still tap the map to choose a location.",
                    2: "Your location could not be determined. Try again or tap the map.",
                    3: "The location request timed out. Try again or tap the map."
                };
                status.textContent = messages[err.code] || "Unable to get your location. Tap the map instead.";
                setWarning(true);
            }
        });
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

    /* ---------- Live camera ----------
       "Take a photo" opens a live preview using the browser's camera access.
       Browsers allow that only on https:// pages (or localhost). Anywhere it is
       unavailable, denied, or fails, the plain file picker (which opens the
       phone's camera app on mobile) is offered instead, so a photo can always
       be attached. Without JavaScript the label still opens the file picker. */
    var cameraLabel = document.getElementById("camera-label");
    var modal = document.getElementById("camera-modal");
    var video = document.getElementById("camera-video");
    var cameraStatus = document.getElementById("camera-status");
    var captureButton = document.getElementById("camera-capture");
    var switchButton = document.getElementById("camera-switch");
    var fallbackButton = document.getElementById("camera-fallback");
    var cancelButton = document.getElementById("camera-cancel");
    var stream = null;
    var facing = "environment";
    var openerFocus = null;
    var requestId = 0;           /* ignores a stream that arrives after Cancel */
    var CAPTURE_MAX_SIDE = 1920; /* keeps photos far below the 10 MB limit */

    function liveCameraSupported() {
        return !!(modal && video && navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.isSecureContext !== false);
    }

    function stopStream() {
        if (stream) { stream.getTracks().forEach(function (t) { t.stop(); }); stream = null; }
        video.srcObject = null;
    }

    function setCameraMessage(text, isProblem) {
        cameraStatus.textContent = text;
        cameraStatus.classList.toggle("is-warning", !!isProblem);
    }

    function closeCamera() {
        requestId += 1;
        stopStream();
        modal.hidden = true;
        document.body.classList.remove("camera-open");
        captureButton.disabled = true;
        if (openerFocus && openerFocus.focus) { openerFocus.focus(); }
        openerFocus = null;
    }

    function useFilePicker() {
        closeCamera();
        cameraInput.click();   /* a fresh tap, so the browser allows the picker */
    }

    function cameraProblem(err) {
        var name = err && err.name;
        var text;
        if (name === "NotAllowedError" || name === "SecurityError") {
            text = "Camera permission was denied. Allow it in your browser settings, or choose a photo from your device instead.";
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
            text = "No camera was found on this device. You can choose a photo from your device instead.";
        } else if (name === "NotReadableError" || name === "AbortError") {
            text = "The camera is being used by another app. Close it and try again, or choose a photo instead.";
        } else {
            text = "The camera could not be started. You can choose a photo from your device instead.";
        }
        setCameraMessage(text, true);
        captureButton.disabled = true;
        switchButton.hidden = true;
        fallbackButton.hidden = false;
        fallbackButton.focus();
    }

    function startStream() {
        var myRequest = ++requestId;
        stopStream();
        captureButton.disabled = true;
        fallbackButton.hidden = true;
        setCameraMessage("Starting camera… allow access if your browser asks.", false);
        navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: facing }, width: { ideal: 1920 }, height: { ideal: 1080 } },
            audio: false
        }).then(function (s) {
            if (myRequest !== requestId) { s.getTracks().forEach(function (t) { t.stop(); }); return; }
            stream = s;
            video.srcObject = s;
            var played = video.play && video.play();
            if (played && played.catch) { played.catch(function () { /* autoplay is already set */ }); }
            setCameraMessage("Point the camera at the incident, then press Capture.", false);
            /* Offer "Switch camera" only when the device has more than one. */
            if (navigator.mediaDevices.enumerateDevices) {
                navigator.mediaDevices.enumerateDevices().then(function (devices) {
                    var count = devices.filter(function (d) { return d.kind === "videoinput"; }).length;
                    switchButton.hidden = count < 2;
                }).catch(function () { switchButton.hidden = true; });
            }
            captureButton.disabled = !(video.videoWidth > 0);
        }).catch(function (err) {
            if (myRequest !== requestId) { return; }
            cameraProblem(err);
        });
    }

    function openCamera() {
        openerFocus = document.activeElement;
        modal.hidden = false;
        document.body.classList.add("camera-open");
        switchButton.hidden = true;
        startStream();
        cancelButton.focus();
    }

    if (video) {
        video.addEventListener("loadedmetadata", function () { captureButton.disabled = !stream; });
        video.addEventListener("playing", function () { captureButton.disabled = !stream; });
    }

    function capturePhoto() {
        var w = video.videoWidth, h = video.videoHeight;
        if (!stream || !w || !h) { setCameraMessage("The camera is not ready yet. Wait a moment and try again.", true); return; }
        var scale = Math.min(1, CAPTURE_MAX_SIDE / Math.max(w, h));
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(w * scale);
        canvas.height = Math.round(h * scale);
        canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
            if (!blob) { cameraProblem(new Error("capture failed")); return; }
            var file;
            try {
                file = new File([blob], "camera-" + Date.now() + ".jpg", { type: "image/jpeg" });
                var transfer = new DataTransfer();
                transfer.items.add(file);
                cameraInput.files = transfer.files;
            } catch (e) {
                /* Very old browsers cannot fill a file input from code. */
                setCameraMessage("This browser cannot attach a captured photo. Please choose a photo from your device instead.", true);
                fallbackButton.hidden = false;
                return;
            }
            closeCamera();
            onPhoto({ target: cameraInput });
        }, "image/jpeg", 0.85);
    }

    if (cameraLabel && liveCameraSupported()) {
        /* Replace the plain file-picker behaviour with the live preview. */
        cameraLabel.addEventListener("click", function (event) { event.preventDefault(); openCamera(); });
        captureButton.addEventListener("click", capturePhoto);
        cancelButton.addEventListener("click", closeCamera);
        fallbackButton.addEventListener("click", useFilePicker);
        switchButton.addEventListener("click", function () {
            facing = facing === "environment" ? "user" : "environment";
            startStream();
        });
        modal.addEventListener("keydown", function (event) {
            if (event.key === "Escape") { event.preventDefault(); closeCamera(); return; }
            if (event.key !== "Tab") { return; }
            /* Keep keyboard focus inside the open dialog. */
            var items = Array.prototype.filter.call(modal.querySelectorAll("button"), function (b) { return !b.disabled && !b.hidden; });
            if (!items.length) { return; }
            var first = items[0], last = items[items.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        });
        /* Release the camera if the page is hidden or left. */
        window.addEventListener("pagehide", stopStream);
        document.addEventListener("visibilitychange", function () { if (document.hidden && !modal.hidden) { closeCamera(); } });
    }

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
