const { JSDOM, VirtualConsole, CookieJar } = require("jsdom");
const fs = require("fs");
const BASE = "http://127.0.0.1:5055";
let failures = 0, passes = 0;
function check(cond, label) { if (cond) { passes++; console.log("  PASS", label); } else { failures++; console.log("  FAIL", label); } }
const sleep = ms => new Promise(r => setTimeout(r, ms));

function makeJar() {
  const jar = { c: {} };
  jar.take = r => (r.headers.getSetCookie?.() || []).forEach(c => { const [kv] = c.split(";"); const i = kv.indexOf("="); jar.c[kv.slice(0, i)] = kv.slice(i + 1); });
  jar.header = () => Object.entries(jar.c).map(([k, v]) => `${k}=${v}`).join("; ");
  return jar;
}
async function login() {
  const jar = makeJar(); const take = jar.take; const cookie = jar.header;
  let r = await fetch(BASE + "/admin/login"); take(r);
  const token = (await r.text()).match(/name="csrf-token" content="([^"]+)"/)[1];
  r = await fetch(BASE + "/admin/login", { method: "POST", redirect: "manual",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Cookie: cookie() },
    body: new URLSearchParams({ csrf_token: token, username: "admin", password: "correct-horse-battery" }) });
  take(r);
  return jar;
}

async function openMap(cookie, opts = {}) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", e => { if (!/Could not load|tile|Not implemented/.test(String(e.message))) errors.push(String(e.message)); });
  vc.on("error", e => errors.push("console.error: " + e));
  const jar = cookie || makeJar();
  const pageRes = await fetch(BASE + "/map" + (opts.query || ""), { headers: { Cookie: jar.header() } }); jar.take(pageRes);
  const html = await pageRes.text();
  const dom = new JSDOM(html, {
    url: BASE + "/map" + (opts.query || ""), runScripts: "dangerously", resources: "usable", pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(w) {
      Object.defineProperty(w.HTMLElement.prototype, "clientWidth", { get() { return 900; } });
      Object.defineProperty(w.HTMLElement.prototype, "clientHeight", { get() { return 600; } });
      w.SVGElement.prototype.createSVGRect = () => ({});
      w.confirm = () => true;
      w.alert = () => {};
      w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {} });
      w.fetch = (url, o = {}) => {
        const u = new URL(url, BASE);
        if (u.origin !== BASE) return Promise.reject(new TypeError("blocked external " + u.origin));
        if (opts.fetchOverride) { const x = opts.fetchOverride(u, o); if (x) return x; }
        return fetch(u, { ...o, headers: { ...(o.headers || {}), Cookie: jar.header() } }).then(r => { jar.take(r); return r; });
      };
    }
  });
  await new Promise(r => dom.window.addEventListener("load", r));
  await sleep(700);
  return { dom, w: dom.window, d: dom.window.document, errors };
}
const markers = d => [...d.querySelectorAll(".tmarker")].filter(m => !m.classList.contains("selecting"));
const cats = d => markers(d).map(m => [...m.classList].find(c => c.startsWith("cat-"))).sort();
const serverMarkings = async () => (await (await fetch(BASE + "/api/map-markings")).json()).markings;

(async () => {
  console.log("PUBLIC VISITOR");
  let { w, d, errors } = await openMap(null);
  check(markers(d).length === 5, "5 markers rendered, one per saved marking");
  check(JSON.stringify(cats(d)) === JSON.stringify(["cat-evacuation_center","cat-facility","cat-hazard","cat-incident","cat-other"]), "each category has its own colour class");
  check(markers(d).every(m => m.querySelector("svg")), "every marker contains an icon, not colour alone");
  check([...d.querySelectorAll("[data-count]")].every(n => n.textContent === "1"), "category counts show 1 each");
  check(d.querySelectorAll("#legend-list li").length === 5, "legend lists all 5 categories");
  check(!d.getElementById("add-marking") && !d.getElementById("marking-form"), "no admin controls in public page");
  const incidentBox = d.querySelector('.category-filter[value="incident"]');
  for (let i = 1; i <= 3; i++) {
    incidentBox.checked = false; incidentBox.dispatchEvent(new w.Event("change"));
    const hidden = markers(d).length === 4 && !cats(d).includes("cat-incident");
    incidentBox.checked = true; incidentBox.dispatchEvent(new w.Event("change"));
    check(hidden && markers(d).length === 5, `filter off/on round ${i}: marker hidden then restored`);
  }
  // popup, public: no edit/delete
  markers(d)[0].closest(".leaflet-marker-icon").dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  await sleep(100);
  const pop = d.querySelector(".leaflet-popup-content");
  check(pop && pop.textContent.length > 5, "popup opens with content");
  check(pop && !pop.querySelector("button"), "public popup has no edit/delete buttons");
  check(pop && !pop.querySelector("b") && pop.textContent.includes("<b>desc</b>"), "description rendered as text, not HTML");
  const toggle = d.getElementById("show-barangays");
  toggle.checked = true; toggle.dispatchEvent(new w.Event("change")); await sleep(300);
  const paths = () => d.querySelectorAll(".leaflet-barangays-pane path").length;
  check(paths() === 21, `barangay toggle on draws 21 outlines (got ${paths()})`);
  toggle.checked = false; toggle.dispatchEvent(new w.Event("change")); await sleep(100);
  check(paths() === 0, "barangay toggle off removes outlines");
  check(errors.length === 0, "no JavaScript errors (public)" + (errors.length ? ": " + errors.join(" | ") : ""));
  w.close();

  console.log("ROBUSTNESS");
  ({ w, d, errors } = await openMap(null, { fetchOverride: (u) => u.pathname === "/api/map-markings" ? Promise.resolve(new Response("<html>login</html>", { status: 200 })) : null }));
  check(/unexpected response/i.test(d.getElementById("map-message").textContent), "HTML instead of JSON gives a readable error");
  check(markers(d).length === 0 && !d.getElementById("map-loading") || d.getElementById("map-loading").hidden, "map stays usable and loading indicator clears");
  w.close();
  ({ w, d } = await openMap(null, { fetchOverride: (u) => u.pathname.endsWith(".geojson") ? Promise.resolve(new Response("nope", { status: 500 })) : null }));
  check(d.getElementById("show-barangays").disabled, "boundary load failure disables only the boundary toggle");
  check(markers(d).length === 5, "markers still load when boundaries fail");
  w.close();
  ({ w, d } = await openMap(null, { fetchOverride: (u) => u.hostname === "nominatim.openstreetmap.org" ? Promise.reject(new TypeError("offline")) : null }));
  d.getElementById("place-search").value = "plaza";
  d.getElementById("place-search-form").dispatchEvent(new w.Event("submit", { cancelable: true, bubbles: true }));
  await sleep(300);
  check(/unavailable/i.test(d.getElementById("search-results").textContent) && d.querySelectorAll("#search-results button").length === 1, "search outage: friendly note + saved-marking match still offered");
  w.close();

  console.log("ADMINISTRATOR");
  const cookie = await login();
  ({ w, d, errors } = await openMap(cookie));
  check(!!d.getElementById("add-marking"), "admin sees Add marking control");
  // ADD
  d.getElementById("add-marking").click();
  check(!d.getElementById("marking-form").hidden, "add form opens");
  d.getElementById("marking-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await sleep(50);
  check(!d.querySelector('[data-error-for="name"]').hidden && !d.querySelector('[data-error-for="location"]').hidden, "saving empty form shows inline errors for name and location");
  check((await serverMarkings()).length === 5, "nothing saved without name/coordinates");
  d.getElementById("tanaw-map").dispatchEvent(new w.MouseEvent("click", { bubbles: true, clientX: 450, clientY: 300 }));
  check(/\d+\.\d{6}, \d+\.\d{6}/.test(d.getElementById("selected-coordinates").textContent), "clicking map selects coordinates and shows them");
  d.getElementById("marking-name").value = "New Shelter";
  d.getElementById("marking-category").value = "evacuation_center";
  d.getElementById("marking-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await sleep(500);
  check(markers(d).length === 6, "new marking appears without page reload");
  check((await serverMarkings()).length === 6, "new marking persisted on server");
  check(d.querySelector('[data-count="evacuation_center"]').textContent === "2", "category count updated to 2");
  // reload persistence
  const again = await openMap(cookie);
  check(markers(again.d).length === 6, "after fresh page load all 6 markings still shown (no duplicates)");
  again.w.close();
  // EDIT (no location change)
  const before = (await serverMarkings()).find(m => m.name === "Flood Zone");
  const hazardEl = markers(d).find(m => m.classList.contains("cat-hazard")).closest(".leaflet-marker-icon");
  hazardEl.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(100);
  const buttons = [...d.querySelectorAll(".leaflet-popup-content button")];
  check(buttons.length === 2, "admin popup shows Edit and Delete");
  buttons.find(b => /Edit/.test(b.textContent)).click();
  check(d.getElementById("marking-name").value === "Flood Zone" && d.getElementById("marking-form-title").textContent === "Edit map marking", "edit form is prefilled");
  d.getElementById("marking-name").value = "Flood Zone (renamed)";
  d.getElementById("marking-category").value = "facility";
  d.getElementById("marking-form").dispatchEvent(new w.Event("submit", { cancelable: true }));
  await sleep(500);
  const after = (await serverMarkings()).find(m => m.id === before.id);
  check(after.name === "Flood Zone (renamed)" && after.category === "facility", "edit saved name and category");
  check(after.latitude === before.latitude && after.longitude === before.longitude, "edit without moving pin kept the original location");
  check(markers(d).length === 6 && cats(d).filter(c => c === "cat-hazard").length === 0 && cats(d).filter(c => c === "cat-facility").length === 2, "edited marker moved to new category layer, no duplicate");
  // DELETE
  const newEl = markers(d).find(m => m.classList.contains("cat-evacuation_center")).closest(".leaflet-marker-icon");
  newEl.dispatchEvent(new w.MouseEvent("click", { bubbles: true })); await sleep(100);
  [...d.querySelectorAll(".leaflet-popup-content button")].find(b => /Delete/.test(b.textContent)).click();
  await sleep(500);
  check(markers(d).length === 5 && (await serverMarkings()).length === 5, "delete removes marker and server record");
  // session expiry mid-use
  const expired = await openMap(cookie, { fetchOverride: (u, o) => (o.method === "POST" ? Promise.resolve(new Response(JSON.stringify({ error: "Administrator login required." }), { status: 401 })) : null) });
  expired.d.getElementById("add-marking").click();
  expired.d.getElementById("tanaw-map").dispatchEvent(new expired.w.MouseEvent("click", { bubbles: true, clientX: 300, clientY: 300 }));
  expired.d.getElementById("marking-name").value = "X";
  expired.d.getElementById("marking-form").dispatchEvent(new expired.w.Event("submit", { cancelable: true }));
  await sleep(400);
  check(/session has expired/i.test(expired.d.getElementById("map-message").textContent) && !expired.d.getElementById("save-marking").disabled, "401 on save shows a clear message and re-enables Save");
  expired.w.close();
  // focus + prefill params
  const pre = await openMap(cookie, { query: "?add=1&lat=15.001&lng=120.651&name=Prefilled&category=incident&location=Road" });
  check(pre.d.getElementById("marking-name").value === "Prefilled" && /15\.001000/.test(pre.d.getElementById("selected-coordinates").textContent), "?add=1 prefill from a report works");
  pre.w.close();
  // specific types, status/expiry, directions links
  const typed = await openMap(cookie); const td = typed.d, tw = typed.w;
  const baseCount = markers(td).length;
  td.getElementById("add-marking").click();
  td.getElementById("marking-category").value = "hazard";
  td.getElementById("marking-category").dispatchEvent(new tw.Event("change"));
  const typeValues = [...td.getElementById("marking-type").options].map(o => o.value);
  check(["flooded_road", "road_construction", "closed_road", "road_damage"].every(v => typeValues.includes(v)) && !typeValues.includes("fire_station"), "type list follows the chosen category");
  check(td.getElementById("marking-status-fields").hidden, "status/expiry hidden until a temporary type is chosen");
  td.getElementById("marking-type").value = "flooded_road";
  td.getElementById("marking-type").dispatchEvent(new tw.Event("change"));
  check(!td.getElementById("marking-status-fields").hidden, "status/expiry shown for a temporary road hazard");
  td.getElementById("tanaw-map").dispatchEvent(new tw.MouseEvent("click", { bubbles: true, clientX: 420, clientY: 280 }));
  td.getElementById("marking-name").value = "Flooded Road Test";
  td.getElementById("marking-status").value = "resolved";
  td.getElementById("marking-expires").value = "2999-12-31";
  td.getElementById("marking-form").dispatchEvent(new tw.Event("submit", { cancelable: true }));
  await sleep(500);
  const saved = (await serverMarkings()).find(m => m.name === "Flooded Road Test");
  check(saved && saved.marking_type === "flooded_road" && saved.status === "resolved" && saved.expires_at === "2999-12-31" && saved.is_active === false, "type, status and expiry saved on the server");
  check(markers(td).length === baseCount + 1 && td.getElementById("show-inactive").checked, "resolved marking is shown after saving (inactive filter turned on)");
  const publicPage = await openMap(null);
  check(markers(publicPage.d).length === baseCount, "public map hides resolved markings by default");
  const inactiveBox = publicPage.d.getElementById("show-inactive");
  inactiveBox.checked = true; inactiveBox.dispatchEvent(new publicPage.w.Event("change"));
  check(markers(publicPage.d).length === baseCount + 1, "'Resolved or expired' filter reveals it");
  check(publicPage.d.querySelectorAll("#legend-types li").length === 9, "legend lists the 9 marking types");
  const newest = markers(publicPage.d).map(m => m.closest(".leaflet-marker-icon")).find(e => /Flooded Road Test/.test(e.title));
  newest.dispatchEvent(new publicPage.w.MouseEvent("click", { bubbles: true })); await sleep(100);
  const hrefs = [...publicPage.d.querySelectorAll(".leaflet-popup-content a")].map(a => a.href);
  check(hrefs.some(h => h.startsWith("https://www.google.com/maps/dir/?api=1&destination=")) && hrefs.some(h => h.startsWith("https://waze.com/ul?ll=") && h.includes("navigate=yes")), "popup offers Open in Google Maps and Open in Waze");
  check(/Resolved/.test(publicPage.d.querySelector(".leaflet-popup-content").textContent) && /Flooded road/.test(publicPage.d.querySelector(".leaflet-popup-content").textContent), "popup shows the type and resolved state");
  check(publicPage.errors.length === 0 && typed.errors.length === 0, "no JavaScript errors (types)");
  publicPage.w.close(); tw.close();
  check(errors.length === 0, "no JavaScript errors (admin)" + (errors.length ? ": " + errors.join(" | ") : ""));
  w.close();

  console.log(`\n${passes} passed, ${failures} failed`);
  process.exit(failures ? 1 : 0);
})().catch(e => { console.error("HARNESS ERROR", e); process.exit(2); });
