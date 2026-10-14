/* Radar Appart — tableau de bord.
 * Lit et écrit les fichiers JSON du dépôt privé de données via l'API GitHub (jeton stocké dans ce navigateur).
 *   listings.json : annonces (écrit par le robot)
 *   state.json    : santé des sources (écrit par le robot)
 *   config.json   : critères, zone, alertes, agences (écrit par ce tableau de bord)
 *   tracking.json : suivi « intéressé / visité… » et notes (écrit par ce tableau de bord)
 */
"use strict";

const STATUS = {
  match: "Correspond", a_verifier: "À vérifier", negociable: "Négociable", rejete: "Écartée",
};
const TRACK = {
  interesse: "⭐ Intéressant", contacte: "📞 Contacté", visite_prevue: "📅 Visite prévue",
  visite: "👀 Visité", offre: "✍️ Offre faite", rejete: "✖ Masquée (pas pour nous)",
};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const eur = (v) => (v ? Math.round(v).toLocaleString("fr-FR") + " €" : "—");
const fmtDate = (s) => (s ? new Date(s).toLocaleString("fr-FR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");
const ago = (s) => {
  if (!s) return "jamais";
  const h = (Date.now() - new Date(s)) / 36e5;
  if (h < 1) return `il y a ${Math.max(1, Math.round(h * 60))} min`;
  if (h < 48) return `il y a ${Math.round(h)} h`;
  return `il y a ${Math.round(h / 24)} j`;
};

let S = { config: null, configSha: null, listings: {}, state: {}, tracking: { items: {} }, trackingSha: null };
let ui = { status: new Set(["match", "a_verifier", "negociable"]) };
let maps = {};

/* ---------------- GitHub API ---------------- */
const conn = () => { try { return JSON.parse(localStorage.getItem("radar-conn") || "null"); } catch { return null; } };

async function gh(path, opts = {}) {
  const c = conn();
  const r = await fetch(`https://api.github.com${path}`, {
    cache: "no-store",
    ...opts,
    headers: { Authorization: `Bearer ${c.token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", ...(opts.headers || {}) },
  });
  if (!r.ok) {
    const err = new Error(`GitHub ${r.status} sur ${path}`);
    err.status = r.status;
    throw err;
  }
  return r;
}

function b64decode(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}
function b64encode(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

async function readJson(file, fallback) {
  const repo = conn().dataRepo;
  try {
    const meta = await (await gh(`/repos/${repo}/contents/${file}`)).json();
    let text = meta.content ? b64decode(meta.content) : null;
    if (!text) text = await (await gh(`/repos/${repo}/contents/${file}`, { headers: { Accept: "application/vnd.github.raw+json" } })).text();
    return { data: JSON.parse(text), sha: meta.sha };
  } catch (e) {
    if (e.status === 404) return { data: structuredClone(fallback), sha: null };
    throw e;
  }
}

async function writeJson(file, data, sha, message) {
  const body = { message, content: b64encode(JSON.stringify(data, null, 1)) };
  if (sha) body.sha = sha;
  const r = await gh(`/repos/${conn().dataRepo}/contents/${file}`, { method: "PUT", body: JSON.stringify(body) });
  return (await r.json()).content.sha;
}

/* Lecture-modification-écriture avec nouvelle tentative si quelqu'un a écrit entre-temps. */
async function updateJson(file, fallback, mutate, message) {
  for (let attempt = 0; attempt < 4; attempt++) {
    const { data, sha } = await readJson(file, fallback);
    mutate(data);
    try {
      const newSha = await writeJson(file, data, sha, message);
      return { data, sha: newSha };
    } catch (e) {
      if (e.status !== 409 && e.status !== 422) throw e;
    }
  }
  throw new Error("Conflit d'écriture répété, réessayez.");
}

/* ---------------- Interface générale ---------------- */
function toast(msg, isError = false) {
  const t = $("#toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.add("hidden"), isError ? 7000 : 3000);
}

function showTab(name) {
  $$("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab").forEach((s) => s.classList.toggle("hidden", s.id !== `tab-${name}`));
  try { localStorage.setItem("radar-tab", name); } catch {}
  if (name === "carte") renderMap();
  if (name === "zone") renderZoneMap();
}

async function loadAll() {
  $("#last-update").textContent = "chargement…";
  const [cfg, lst, st, tr] = await Promise.all([
    readJson("config.json", {}),
    readJson("listings.json", { listings: {} }),
    readJson("state.json", {}),
    readJson("tracking.json", { items: {} }),
  ]);
  S.config = cfg.data; S.configSha = cfg.sha;
  S.listings = lst.data.listings || {};
  S.updatedAt = lst.data.updated_at;
  S.state = st.data;
  S.tracking = tr.data; S.tracking.items ||= {};
  $("#last-update").textContent = S.updatedAt ? `mis à jour ${ago(S.updatedAt)}` : "aucun passage encore";
  renderListings(); renderCriteria(); renderAlerts(); renderSources(); renderVerifyStats();
  const active = localStorage.getItem("radar-tab");
  if (active === "carte" || active === "zone") showTab(active);
}

async function saveConfig(mutate, label) {
  const res = await updateJson("config.json", {}, mutate, `Tableau de bord : ${label}`);
  S.config = res.data; S.configSha = res.sha;
  toast(`✅ ${label} enregistré(e). Pris en compte au prochain passage du robot.`);
}

/* ---------------- Annonces ---------------- */
function listingText(l) {
  return [l.title, l.description, l.agency, l.postcode, ...(l.sources || []).map((s) => s.source)].join(" ").toLowerCase();
}

function renderStatusChips() {
  const counts = {};
  Object.values(S.listings).forEach((l) => { if (l.active !== false || $("#f-inactive").checked) counts[l.status] = (counts[l.status] || 0) + 1; });
  $("#f-status").innerHTML = Object.entries(STATUS).map(([k, v]) =>
    `<button class="chip ${ui.status.has(k) ? "on" : ""}" data-s="${k}">${v} (${counts[k] || 0})</button>`).join("");
  $$("#f-status .chip").forEach((b) => b.onclick = () => {
    ui.status.has(b.dataset.s) ? ui.status.delete(b.dataset.s) : ui.status.add(b.dataset.s);
    renderListings();
  });
}

function filteredListings() {
  const track = $("#f-track").value, q = $("#f-search").value.trim().toLowerCase(), inactive = $("#f-inactive").checked;
  let arr = Object.values(S.listings).filter((l) => {
    if (!ui.status.has(l.status)) return false;
    if (!inactive && l.active === false) return false;
    const t = S.tracking.items[l.id]?.status || "";
    if (!track && t === "rejete") return false;  // masquées : visibles seulement via le filtre de suivi
    if (track === "__none" && t) return false;
    if (track && track !== "__none" && t !== track) return false;
    if (q && !listingText(l).includes(q)) return false;
    return true;
  });
  const sort = $("#f-sort").value;
  const ppm2 = (l) => (l.price && l.surface ? l.price / l.surface : 1e9);
  const cmp = {
    score: (a, b) => (b.score || 0) - (a.score || 0) || (b.first_seen || "").localeCompare(a.first_seen || ""),
    date: (a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""),
    price: (a, b) => (a.price || 1e12) - (b.price || 1e12),
    ppm2: (a, b) => ppm2(a) - ppm2(b),
    total: (a, b) => (a.total_cost || 1e12) - (b.total_cost || 1e12),
  }[sort];
  return arr.sort(cmp);
}

function cardHtml(l) {
  const t = S.tracking.items[l.id] || {};
  const photo = (l.photos || [])[0];
  const isNew = l.first_seen && Date.now() - new Date(l.first_seen) < 36 * 36e5;
  const facts = [
    l.surface && `${l.surface} m²`, l.rooms && `${l.rooms} pièces`,
    l.bedrooms_eff != null && `${l.bedrooms_eff} ch.`,
    l.floor != null && (l.floor === 0 ? "RDC" : `${l.floor}e étage`),
    l.elevator === true ? "ascenseur" : l.elevator === false ? "sans ascenseur" : null,
    l.postcode, l.agency,
    l.geo_place && `📍 d'après « ${l.geo_place} »`,
  ].filter(Boolean).join(" · ");
  const ppm2 = l.price && l.surface ? `${Math.round(l.price / l.surface).toLocaleString("fr-FR")} €/m²` : "";
  const b = l.cost_breakdown;
  const total = l.total_cost ? `<div class="total">Coût total estimé <b>${eur(l.total_cost)}</b>
      <span class="muted">(${eur(b?.price)} + notaire ${eur(b?.notary)} + travaux ${eur(b?.works)} — état : ${{ heavy: "à rénover", refresh: "à rafraîchir", none: "refait", unknown: "inconnu" }[l.state] || "?"}${l.architect ? ", archi" : ""})</span></div>` : "";
  const plus = (l.score_details || []).map((d) => `<span class="tag ${d.startsWith("Rénovation standard") ? "minus" : "plus"}">${esc(d)}</span>`).join("");
  const flags = (l.flags || []).map((f) => `<span class="tag">${esc(f)}</span>`).join("");
  const reasons = (l.reasons || []).length ? `<div class="reasons ${l.status === "rejete" ? "rej" : ""}">${l.status === "rejete" ? "Écartée : " : "À vérifier : "}${esc(l.reasons.join(" · "))}</div>` : "";
  const hist = (l.price_history || []).length > 1 ? `<details><summary>Historique des prix (${l.price_history.length})</summary>${l.price_history.map((p) => `${fmtDate(p.date)} : ${eur(p.price)}`).join("<br>")}</details>` : "";
  const srcs = (l.sources || []).map((s) => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.source.replace("agence:", ""))} ↗</a>`).join("");
  const desc = l.description ? `<details><summary>Description</summary><div class="muted">${esc(l.description.slice(0, 1500))}</div></details>` : "";
  const ai = l.ai?.resume ? `<div class="ai">🤖 ${esc(l.ai.resume)}</div>` : "";
  return `<article class="card ${l.active === false ? "inactive" : ""}" data-id="${l.id}">
    <a class="photo" href="${esc(l.url)}" target="_blank" rel="noopener" style="${photo ? `background-image:url('${esc(photo)}')` : ""}">
      ${photo ? "" : "🏠"}
      <span class="badge-status ${l.status}">${STATUS[l.status] || l.status}</span>
      <span class="score">${l.score ?? 0}</span>
      ${l.active === false ? '<span class="new" style="background:#555">Retirée</span>' : isNew ? '<span class="new">Nouveau</span>' : ""}
    </a>
    <div class="body">
      <div class="price">${eur(l.price)} <small>${ppm2}</small></div>
      <div class="title">${esc((l.title || "").slice(0, 110))}</div>
      <div class="facts">${esc(facts)}</div>
      ${total}
      <div class="tags">${plus}${flags}</div>
      ${reasons}${ai}
      <div class="sources">${srcs}</div>
      <div class="muted">Vue ${fmtDate(l.first_seen)}${l.relisted_count ? ` · remise en ligne ×${l.relisted_count}` : ""}</div>
      ${hist}${desc}
    </div>
    <div class="track">
      <button type="button" class="btn danger small t-hide">${t.status === "rejete" ? "↩ Réafficher" : "✖ Masquer cette annonce"}</button>
      <select class="t-status"><option value="">Suivi…</option>${Object.entries(TRACK).map(([k, v]) => `<option value="${k}" ${t.status === k ? "selected" : ""}>${v}</option>`).join("")}</select>
      <textarea class="t-notes" placeholder="Notes partagées…">${esc(t.notes || "")}</textarea>
    </div>
  </article>`;
}

function renderListings() {
  if (!S.config) return;
  renderStatusChips();
  const arr = filteredListings();
  const total = Object.values(S.listings).filter((l) => l.active !== false && l.status !== "rejete").length;
  $("#counts").textContent = `${arr.length} annonce(s) affichée(s) · ${total} annonce(s) active(s) retenue(s) · ${Object.keys(S.listings).length} en base`;
  $("#cards").innerHTML = arr.slice(0, 300).map(cardHtml).join("") || `<div class="panel">Aucune annonce pour ces filtres.${Object.keys(S.listings).length ? "" : " Le robot n'a pas encore tourné : cliquez sur ▶ Lancer."}</div>`;
  $$("#cards .card").forEach((card) => {
    const id = card.dataset.id;
    $(".t-status", card).onchange = (e) => saveTracking(id, { status: e.target.value }).then(renderListings);
    $(".t-hide", card).onclick = async () => {
      const hidden = S.tracking.items[id]?.status === "rejete";
      card.style.opacity = ".3";
      await saveTracking(id, { status: hidden ? "" : "rejete" },
        hidden ? "Annonce réaffichée" : "Annonce masquée — retrouvable avec le filtre « Suivi : Masquée »");
      renderListings();
    };
    $(".t-notes", card).onchange = (e) => saveTracking(id, { notes: e.target.value });
  });
}

async function saveTracking(id, patch, message = "Suivi enregistré") {
  try {
    const res = await updateJson("tracking.json", { items: {} }, (d) => {
      d.items ||= {};
      d.items[id] = { ...(d.items[id] || {}), ...patch, updated_at: new Date().toISOString() };
    }, `Suivi annonce ${id}`);
    S.tracking = res.data; S.trackingSha = res.sha;
    toast(message);
  } catch (e) { toast("Échec de l'enregistrement : " + e.message, true); }
}

/* ---------------- Cartes ---------------- */
function baseMap(el) {
  const m = L.map(el).setView([48.866, 2.38], 14);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap" }).addTo(m);
  return m;
}

function zoneLayer(style) {
  return L.geoJSON(S.config?.zone || { type: "FeatureCollection", features: [] }, { style: style || { color: "#2f5d50", weight: 2, fillOpacity: 0.08 } });
}

function renderMap() {
  if (!S.config) return;
  if (!maps.main) maps.main = baseMap("map");
  const m = maps.main;
  if (maps.mainLayer) maps.mainLayer.remove();
  const group = L.featureGroup();
  zoneLayer({ color: "#2f5d50", weight: 2, fillOpacity: 0.05, interactive: false }).addTo(group);
  const colors = { match: "#2e8b57", a_verifier: "#c98a00", negociable: "#3a6ea5" };
  Object.values(S.listings).forEach((l) => {
    if (l.active === false || l.status === "rejete" || l.lat == null || S.tracking.items[l.id]?.status === "rejete") return;
    L.circleMarker([l.lat, l.lon], { radius: 7 + (l.score || 0) / 20, color: "#fff", weight: 1.5, fillColor: colors[l.status], fillOpacity: 0.9 })
      .bindPopup(popupHtml(l), { minWidth: 220 })
      .addTo(group);
  });
  group.addTo(m);
  maps.mainLayer = group;
  if (!maps.mainPopupBound) {
    maps.mainPopupBound = true;
    m.on("popupopen", (e) => {
      const el = e.popup.getElement(), box = el.querySelector(".pop");
      if (!box) return;
      const id = box.dataset.id;
      el.querySelector(".pop-open").onclick = () => { m.closePopup(); openListing(id); };
      el.querySelector(".pop-hide").onclick = async () => {
        m.closePopup();
        await saveTracking(id, { status: "rejete" }, "Annonce masquée — retrouvable avec le filtre « Suivi : Masquée »");
        renderMap(); renderListings();
      };
    });
  }
  setTimeout(() => {
    m.invalidateSize();
    // Cadrage automatique seulement à la première ouverture (sinon on garde le zoom de l'utilisateur)
    if (!maps.mainFitted && group.getLayers().length) { m.fitBounds(group.getBounds().pad(0.1)); maps.mainFitted = true; }
  }, 50);
}

function popupHtml(l) {
  const photo = (l.photos || [])[0];
  const t = S.tracking.items[l.id]?.status;
  return `<div class="pop" data-id="${l.id}">
    ${photo ? `<div class="pop-photo" style="background-image:url('${esc(photo)}')"></div>` : ""}
    <div><span class="badge-status ${l.status}">${STATUS[l.status]}</span> <b>score ${l.score ?? 0}</b>${t ? ` · ${TRACK[t]}` : ""}</div>
    <div><b>${eur(l.price)}</b> · ${l.surface || "?"} m²${l.rooms ? ` · ${l.rooms} p.` : ""}</div>
    <div>${esc((l.title || "").slice(0, 80))}</div>
    ${l.geo_precision === "exact" ? "" : "<div class='muted'>position approximative</div>"}
    <div class="pop-actions">
      <button type="button" class="btn small pop-open">Voir la fiche</button>
      <button type="button" class="btn danger small pop-hide">✖ Masquer</button>
    </div>
    <a href="${esc(l.url)}" target="_blank" rel="noopener">Annonce d'origine ↗</a>
  </div>`;
}

/* Ouvre la fiche d'une annonce dans l'onglet Annonces, en ajustant les filtres pour qu'elle soit visible. */
function openListing(id) {
  const l = S.listings[id];
  if (!l) return;
  ui.status.add(l.status);
  $("#f-search").value = "";
  $("#f-track").value = S.tracking.items[id]?.status === "rejete" ? "rejete" : "";
  if (l.active === false) $("#f-inactive").checked = true;
  showTab("annonces");
  renderListings();
  const card = document.querySelector(`.card[data-id="${id}"]`);
  if (card) {
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.add("highlight");
    setTimeout(() => card.classList.remove("highlight"), 2500);
  }
}

function renderZoneMap() {
  if (!S.config) return;
  if (!maps.zone) {
    const m = baseMap("zonemap");
    const drawn = new L.FeatureGroup().addTo(m);
    m.addControl(new L.Control.Draw({
      edit: { featureGroup: drawn },
      draw: { polygon: { allowIntersection: false, shapeOptions: { color: "#2f5d50" } }, rectangle: { shapeOptions: { color: "#2f5d50" } },
        polyline: false, circle: false, marker: false, circlemarker: false },
    }));
    m.on(L.Draw.Event.CREATED, (e) => drawn.addLayer(e.layer));
    maps.zone = m; maps.drawn = drawn;
    zoneLayer().eachLayer((layer) => drawn.addLayer(layer));
  }
  setTimeout(() => { maps.zone.invalidateSize(); if (maps.drawn.getLayers().length) maps.zone.fitBounds(maps.drawn.getBounds().pad(0.15)); }, 50);
}

async function saveZone() {
  const fc = maps.drawn.toGeoJSON();
  fc.features = fc.features.filter((f) => ["Polygon", "MultiPolygon"].includes(f.geometry.type));
  try {
    await saveConfig((c) => { c.zone = fc; }, `Zone (${fc.features.length} polygone(s))`);
  } catch (e) { toast("Échec : " + e.message, true); }
}

/* ---------------- Critères ---------------- */
const num = (id) => { const v = parseFloat($(id).value); return Number.isFinite(v) ? v : null; };
const lines = (id) => $(id).value.split(/\n|,/).map((s) => s.trim()).filter(Boolean);

function renderCriteria() {
  const c = S.config, b = c.budget || {}, w = b.works_per_m2 || {}, bd = c.bedrooms || {};
  $("#crit-form").innerHTML = `
    <h2>Budget</h2>
    <div class="grid2">
      <label>Budget total maximum (€)<input id="c-max" type="number" value="${b.total_max ?? ""}"></label>
      <label>Budget total minimum (€)<input id="c-min" type="number" value="${b.total_min ?? ""}"></label>
      <label>Frais de notaire (%)<input id="c-notary" type="number" step="0.1" value="${((b.notary_rate ?? 0.075) * 100).toFixed(1)}"></label>
      <label>Marge « négociable » au-dessus du budget (%)<input id="c-nego" type="number" step="0.5" value="${((b.negotiable_margin ?? 0.05) * 100).toFixed(1)}"></label>
    </div>
    <p class="help">Coût total = prix affiché (frais d'agence inclus) + notaire + travaux estimés selon l'état décrit dans l'annonce.</p>
    <div class="grid2">
      <label>Travaux si « à rénover » (€/m²)<input id="c-w-heavy" type="number" value="${w.heavy ?? 1500}"></label>
      <label>Travaux si « à rafraîchir » (€/m²)<input id="c-w-refresh" type="number" value="${w.refresh ?? 500}"></label>
      <label>Travaux si déjà refait (€/m²)<input id="c-w-none" type="number" value="${w.none ?? 0}"></label>
      <label>Travaux si état inconnu (€/m²)<input id="c-w-unknown" type="number" value="${w.unknown ?? 500}"></label>
    </div>
    <h2>Le bien</h2>
    <div class="grid2">
      <label>Surface minimum (m²)<input id="c-smin" type="number" value="${c.surface?.min ?? ""}"></label>
      <label>Surface idéale (m²)<input id="c-sideal" type="number" value="${c.surface?.ideal ?? ""}"></label>
      <label>Chambres minimum<input id="c-bmin" type="number" value="${bd.min ?? 3}"></label>
      <label>Ascenseur obligatoire à partir de l'étage<input id="c-elev" type="number" value="${c.elevator_required_from_floor ?? 1}"></label>
    </div>
    <label class="check"><input type="checkbox" id="c-bposs" ${bd.allow_one_less_if_possible ? "checked" : ""}> Accepter une chambre de moins si une chambre supplémentaire est faisable</label>
    <div class="grid2">
      <label>… si la surface atteint (m²)<input id="c-bposs-s" type="number" value="${bd.possible_min_surface ?? 90}"></label>
      <label>… ou si le nombre de pièces atteint<input id="c-bposs-r" type="number" value="${bd.possible_min_rooms ?? 4}"></label>
    </div>
    <label>Exclure les immeubles construits à partir de (année)<input id="c-year" type="number" value="${c.exclude_built_from_year ?? 1960}"></label>
    <label>Codes postaux acceptés (séparés par des virgules)<input id="c-pc" value="${esc((c.postcodes || []).join(", "))}"></label>
    <label>Tolérance autour de la zone dessinée (mètres)<input id="c-tol" type="number" value="${c.zone_tolerance_m ?? 0}"></label>
    <p class="help">0 = exclusion stricte. Les portails floutent la position des biens (≈ 250 m) : une tolérance de 200–300 m garde, en « à vérifier », les biens dont la position floutée tombe juste hors de la zone.</p>
    <label>Mots-clés éliminatoires (un par ligne)<textarea id="c-excl" rows="6">${esc((c.exclude_keywords || []).join("\n"))}</textarea></label>
    <h2>Score (points par critère « bonus »)</h2>
    <p class="help">Les mots-clés sont cherchés dans le titre et la description (accents et majuscules ignorés). Points négatifs = pénalité.</p>
    <div id="score-rows">${(c.scoring || []).map((r, i) => `
      <div class="score-row" data-i="${i}">
        <input class="s-label" value="${esc(r.label)}">
        <input class="s-points" type="number" value="${r.points}">
        <input class="s-kw kw" value="${esc((r.keywords || []).join(", "))}" placeholder="${r.field ? "(détection automatique)" : "mots-clés, séparés par des virgules"}">
        <button type="button" class="btn danger small s-del">✕</button>
      </div>`).join("")}</div>
    <button type="button" id="btn-add-score" class="btn ghost small">+ Ajouter un critère bonus</button>
    <h2>Options</h2>
    <label class="check"><input type="checkbox" id="c-enrich" ${c.enrich_detail_pages !== false ? "checked" : ""}> Ouvrir la fiche complète des annonces reçues par email (plus d'infos)</label>
    <label class="check"><input type="checkbox" id="c-gemini" ${c.gemini?.enabled ? "checked" : ""}> Analyse IA Gemini (texte + photos) — nécessite le secret GEMINI_API_KEY</label>
    <label class="check"><input type="checkbox" id="c-bienici" ${c.sources?.bienici?.enabled !== false ? "checked" : ""}> Source Bien'ici (collecte directe)</label>
    <label class="check"><input type="checkbox" id="c-gmail" ${c.sources?.gmail?.enabled !== false ? "checked" : ""}> Source alertes email (Gmail)</label>
    <label class="check"><input type="checkbox" id="c-web" ${c.sources?.websearch?.enabled !== false ? "checked" : ""}> Recherche web « filet de sécurité » quotidienne</label>
    <label>Requêtes de la recherche web (une par ligne)<textarea id="c-queries" rows="5">${esc((c.sources?.websearch?.queries || []).join("\n"))}</textarea></label>`;
  $$("#score-rows .s-del").forEach((btn) => btn.onclick = () => btn.closest(".score-row").remove());
  $("#btn-add-score").onclick = () => {
    $("#score-rows").insertAdjacentHTML("beforeend", `<div class="score-row" data-i="new"><input class="s-label" placeholder="Nom"><input class="s-points" type="number" value="5">
      <input class="s-kw kw" placeholder="mots-clés, séparés par des virgules"><button type="button" class="btn danger small s-del">✕</button></div>`);
    $$("#score-rows .s-del").forEach((btn) => btn.onclick = () => btn.closest(".score-row").remove());
  };
}

async function saveCriteria() {
  const old = S.config.scoring || [];
  const scoring = $$("#score-rows .score-row").map((row) => {
    const prev = row.dataset.i !== "new" ? old[+row.dataset.i] : {};
    const r = { label: $(".s-label", row).value.trim(), points: parseFloat($(".s-points", row).value) || 0 };
    if (prev?.field) r.field = prev.field;
    const kw = $(".s-kw", row).value.split(",").map((s) => s.trim()).filter(Boolean);
    if (kw.length) r.keywords = kw;
    return r;
  }).filter((r) => r.label);
  try {
    await saveConfig((c) => {
      c.budget ||= {}; c.budget.works_per_m2 ||= {};
      c.budget.total_max = num("#c-max"); c.budget.total_min = num("#c-min");
      c.budget.notary_rate = num("#c-notary") / 100; c.budget.negotiable_margin = num("#c-nego") / 100;
      Object.assign(c.budget.works_per_m2, { heavy: num("#c-w-heavy"), refresh: num("#c-w-refresh"), none: num("#c-w-none"), unknown: num("#c-w-unknown") });
      c.surface = { min: num("#c-smin"), ideal: num("#c-sideal") };
      c.bedrooms = { min: num("#c-bmin"), allow_one_less_if_possible: $("#c-bposs").checked, possible_min_surface: num("#c-bposs-s"), possible_min_rooms: num("#c-bposs-r") };
      c.elevator_required_from_floor = num("#c-elev");
      c.exclude_built_from_year = num("#c-year");
      c.postcodes = lines("#c-pc");
      c.zone_tolerance_m = num("#c-tol") || 0;
      c.exclude_keywords = $("#c-excl").value.split("\n").map((s) => s.trim()).filter(Boolean);
      c.scoring = scoring;
      c.enrich_detail_pages = $("#c-enrich").checked;
      c.gemini = { ...(c.gemini || {}), enabled: $("#c-gemini").checked };
      c.sources ||= {};
      c.sources.bienici = { ...(c.sources.bienici || {}), enabled: $("#c-bienici").checked };
      c.sources.gmail = { ...(c.sources.gmail || {}), enabled: $("#c-gmail").checked };
      c.sources.websearch = { ...(c.sources.websearch || {}), enabled: $("#c-web").checked, queries: $("#c-queries").value.split("\n").map((s) => s.trim()).filter(Boolean) };
    }, "Critères");
    renderCriteria();
  } catch (e) { toast("Échec : " + e.message, true); }
}

/* ---------------- Alertes ---------------- */
function personRow(p = {}) {
  return `<div class="person">
    <label>Prénom<input class="p-name" value="${esc(p.name || "")}"></label>
    <label>Numéro WhatsApp (format +33…)<input class="p-phone" value="${esc(p.phone || "")}" placeholder="+33612345678"></label>
    <label>Clé CallMeBot<input class="p-key" value="${esc(p.apikey || "")}"></label>
    <div class="row" style="margin:0 0 8px"><button type="button" class="btn ghost small p-test">Tester</button><button type="button" class="btn danger small p-del">✕</button></div>
  </div>`;
}

function renderAlerts() {
  const n = S.config.notifications || {};
  const st = new Set(n.alert_statuses || ["match", "a_verifier", "negociable"]);
  $("#alert-form").innerHTML = `
    <h2>WhatsApp (CallMeBot)</h2>
    <p class="help">Chaque personne active CallMeBot une fois sur son téléphone (voir le guide) et reçoit une clé personnelle.</p>
    <div id="people">${(n.whatsapp || []).map(personRow).join("")}</div>
    <button type="button" id="btn-add-person" class="btn ghost small">+ Ajouter une personne</button>
    <h2>Email</h2>
    <label>Adresses qui reçoivent les alertes si WhatsApp échoue (séparées par des virgules)<input id="a-emails" value="${esc((n.email_to || []).join(", "))}"></label>
    <h2>Quand alerter ?</h2>
    <label class="check"><input type="checkbox" id="a-new" ${n.alert_new !== false ? "checked" : ""}> Nouvelles annonces</label>
    <label class="check"><input type="checkbox" id="a-drop" ${n.alert_price_drop !== false ? "checked" : ""}> Baisses de prix</label>
    <label class="check"><input type="checkbox" id="a-relist" ${n.alert_relisted !== false ? "checked" : ""}> Remises en ligne</label>
    <h3>Pour les annonces au statut</h3>
    ${["match", "a_verifier", "negociable"].map((k) => `<label class="check"><input type="checkbox" class="a-st" value="${k}" ${st.has(k) ? "checked" : ""}> ${STATUS[k]}</label>`).join("")}
    <div class="grid2">
      <label>Score minimum pour alerter (0 = toutes)<input id="a-min" type="number" value="${n.min_score ?? 0}"></label>
      <label>Nombre max de messages par passage<input id="a-maxmsg" type="number" value="${n.max_messages_per_run ?? 12}"></label>
    </div>`;
  const bind = () => {
    $$("#people .p-del").forEach((b) => b.onclick = () => b.closest(".person").remove());
    $$("#people .p-test").forEach((b) => b.onclick = () => {
      const row = b.closest(".person");
      const phone = $(".p-phone", row).value.trim(), key = $(".p-key", row).value.trim();
      if (!phone || !key) return toast("Renseignez le numéro et la clé", true);
      const url = `https://api.callmebot.com/whatsapp.php?phone=${encodeURIComponent(phone)}&text=${encodeURIComponent("✅ Test Radar Appart : les alertes fonctionnent !")}&apikey=${encodeURIComponent(key)}`;
      fetch(url, { mode: "no-cors" }).then(() => toast("Message de test envoyé : vérifiez WhatsApp (quelques secondes)")).catch(() => toast("Échec de l'envoi", true));
    });
  };
  bind();
  $("#btn-add-person").onclick = () => { $("#people").insertAdjacentHTML("beforeend", personRow()); bind(); };
}

async function saveAlerts() {
  const people = $$("#people .person").map((r) => ({ name: $(".p-name", r).value.trim(), phone: $(".p-phone", r).value.replace(/\s/g, ""), apikey: $(".p-key", r).value.trim() }))
    .filter((p) => p.phone);
  try {
    await saveConfig((c) => {
      c.notifications = {
        ...(c.notifications || {}), whatsapp: people, email_to: lines("#a-emails"),
        alert_new: $("#a-new").checked, alert_price_drop: $("#a-drop").checked, alert_relisted: $("#a-relist").checked,
        alert_statuses: $$(".a-st").filter((x) => x.checked).map((x) => x.value),
        min_score: num("#a-min") || 0, max_messages_per_run: num("#a-maxmsg") || 12,
      };
    }, "Réglages d'alertes");
  } catch (e) { toast("Échec : " + e.message, true); }
}

/* ---------------- Sources ---------------- */
function healthLevel(h) {
  if (!h.last_run) return ["—", ""];
  if (h.consecutive_errors >= 3) return ["En panne", "err"];
  if (h.consecutive_errors > 0) return ["Erreur au dernier passage", "warn"];
  if (h.silence_alerted) return ["Silencieuse", "warn"];
  return ["OK", "ok"];
}

function renderSources() {
  const health = S.state.health || {};
  const rows = Object.entries(health).sort().map(([name, h]) => {
    const [label, cls] = healthLevel(h);
    const misses = (h.coverage_misses || []).length ? `<br><span class="warn">${h.coverage_misses.length} bien(s) de cette agence vus sur les portails mais pas sur son site</span>` : "";
    return `<tr><td><b>${esc(name.replace("agence:", "agence "))}</b></td><td class="${cls}">${label}</td><td>${ago(h.last_run)}</td>
      <td>${h.last_nonzero ? ago(h.last_nonzero) : "jamais"}</td>
      <td>${esc(h.info || "")}${h.consecutive_errors ? `<br><span class="err">${esc(h.last_error || "")}</span>` : ""}${misses}</td></tr>`;
  }).join("");
  $("#health").innerHTML = rows ? `<div class="table-wrap"><table><tr><th>Source</th><th>État</th><th>Dernier passage</th><th>Dernière annonce</th><th>Détail</th></tr>${rows}</table></div>`
    : "<p class='muted'>Le robot n'a pas encore tourné.</p>";

  const gm = S.state.gmail || {};
  const sr = Object.entries(gm.senders || {}).map(([k, s]) => `<tr><td>${esc(k)}</td><td>${s.emails}</td><td>${s.listings}</td><td>${s.last_email_at ? ago(new Date(s.last_email_at).toISOString()) : "—"}</td></tr>`).join("");
  const unp = (gm.unparsed || []).slice(-8).reverse().map((u) => `<li>${esc(u.from)} — ${esc(u.subject)}</li>`).join("");
  $("#senders").innerHTML = (sr ? `<div class="table-wrap"><table><tr><th>Expéditeur</th><th>Emails</th><th>Annonces extraites</th><th>Dernier email</th></tr>${sr}</table></div>` : "<p class='muted'>Aucune alerte email reçue pour l'instant.</p>")
    + (unp ? `<h3>Emails de portails sans annonce reconnue</h3><p class="help">Souvent des emails de confirmation. Si ce sont de vraies alertes, le format doit être adapté.</p><ul class="muted">${unp}</ul>` : "");

  renderAgencies();

  const dec = S.tracking.discovered || {};
  const disc = Object.entries(gmDiscovered()).filter(([d]) => dec[d] !== "ignore").sort((a, b) => b[1].count - a[1].count);
  $("#discovered").innerHTML = disc.length ? `<div class="table-wrap"><table><tr><th>Site</th><th>Vu</th><th>Exemples</th><th></th></tr>${disc.map(([d, v]) =>
    `<tr><td><b>${esc(d)}</b>${dec[d] === "added" ? ' <span class="ok">ajouté</span>' : ""}</td><td>${v.count}×</td>
      <td>${v.examples.slice(-2).map((u) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(u.slice(0, 60))}…</a>`).join("<br>")}</td>
      <td><button class="btn ghost small d-add" data-d="${esc(d)}">Ajouter</button> <button class="btn danger small d-ign" data-d="${esc(d)}">Ignorer</button></td></tr>`).join("")}</table></div>`
    : "<p class='muted'>Rien de nouveau pour l'instant.</p>";
  $$(".d-ign").forEach((b) => b.onclick = () => decideDiscovered(b.dataset.d, "ignore"));
  $$(".d-add").forEach((b) => b.onclick = () => {
    const d = b.dataset.d, ex = gmDiscovered()[d].examples[0];
    addAgencyRow({ name: d, enabled: true, list_urls: [`https://${d}/`], link_pattern: `${d.replace(/\./g, "\\.")}/.*\\d`, match_names: [d.split(".")[0]], silence_alert_hours: 168 });
    decideDiscovered(d, "added");
    toast(`Agence ajoutée en bas de la liste : vérifiez l'adresse de la page des biens à vendre (exemple : ${ex}), puis Enregistrer.`);
  });

  const runs = (S.state.runs || []).slice(-12).reverse();
  $("#runs").innerHTML = runs.length ? `<div class="table-wrap"><table><tr><th>Début</th><th>Sources</th><th>Alertes</th></tr>${runs.map((r) =>
    `<tr><td>${fmtDate(r.started_at)}</td><td>${Object.entries(r.sources || {}).map(([k, v]) => `<span class="${v.ok ? "ok" : "err"}">${esc(k.replace("agence:", ""))}</span>${v.ok ? ` ${v.count}${v.new ? ` (+${v.new})` : ""}` : ""}`).join(" · ")}</td><td>${r.alerts ?? 0}</td></tr>`).join("")}</table></div>` : "";
}

const gmDiscovered = () => (S.state.websearch || {}).discovered || {};

async function decideDiscovered(domain, decision) {
  try {
    const res = await updateJson("tracking.json", { items: {} }, (d) => { d.discovered ||= {}; d.discovered[domain] = decision; }, `Site découvert ${domain} : ${decision}`);
    S.tracking = res.data; renderSources();
  } catch (e) { toast("Échec : " + e.message, true); }
}

function agencyRow(a) {
  return `<div class="agency">
    <div class="grid2">
      <label>Nom<input class="ag-name" value="${esc(a.name || "")}"></label>
      <label>Noms dans les annonces des portails (virgules)<input class="ag-match" value="${esc((a.match_names || []).join(", "))}"></label>
    </div>
    <label>Pages listant les biens à vendre (une par ligne)<textarea class="ag-urls" rows="2">${esc((a.list_urls || []).join("\n"))}</textarea></label>
    <div class="grid2">
      <label>Motif des liens de fiche (expression régulière)<input class="ag-pattern" value="${esc(a.link_pattern || "")}"></label>
      <label>Alerte si aucune annonce depuis (heures)<input class="ag-silence" type="number" value="${a.silence_alert_hours ?? 168}"></label>
    </div>
    <div class="row"><label class="check"><input type="checkbox" class="ag-enabled" ${a.enabled !== false ? "checked" : ""}> Active</label>
      <button type="button" class="btn danger small ag-del">Supprimer</button></div>
  </div>`;
}
function renderAgencies() {
  $("#agencies").innerHTML = (S.config.sources?.agencies || []).map(agencyRow).join("");
  $$("#agencies .ag-del").forEach((b) => b.onclick = () => b.closest(".agency").remove());
}
function addAgencyRow(a) {
  $("#agencies").insertAdjacentHTML("beforeend", agencyRow(a));
  $$("#agencies .ag-del").forEach((b) => b.onclick = () => b.closest(".agency").remove());
}
async function saveAgencies() {
  const list = $$("#agencies .agency").map((el) => ({
    name: $(".ag-name", el).value.trim(),
    enabled: $(".ag-enabled", el).checked,
    list_urls: $(".ag-urls", el).value.split("\n").map((s) => s.trim()).filter(Boolean),
    link_pattern: $(".ag-pattern", el).value.trim(),
    match_names: $(".ag-match", el).value.split(",").map((s) => s.trim()).filter(Boolean),
    silence_alert_hours: parseFloat($(".ag-silence", el).value) || 168,
  })).filter((a) => a.name && a.list_urls.length && a.link_pattern);
  for (const a of list) {
    try { new RegExp(a.link_pattern); } catch { return toast(`Motif invalide pour ${a.name}`, true); }
  }
  try {
    await saveConfig((c) => { c.sources ||= {}; c.sources.agencies = list; }, `Agences (${list.length})`);
  } catch (e) { toast("Échec : " + e.message, true); }
}

/* ---------------- Vérifier la couverture ---------------- */
const canon = (u) => (u || "").replace(/[?#].*$/, "").replace(/\/$/, "").replace(/^https?:\/\/(www\.)?/, "").toLowerCase();
const extId = (u) => (u.match(/(\d{6,})(?:\.htm)?(?:[/?#]|$)/) || [])[1];

function findListing(url, price, surface, pc) {
  if (url) {
    const cu = canon(url), id = extId(url);
    for (const l of Object.values(S.listings)) {
      for (const s of l.sources || []) {
        if (canon(s.url) === cu || (id && s.url.includes(id))) return { l, how: "même lien" };
      }
    }
  }
  if (price && surface) {
    for (const l of Object.values(S.listings)) {
      if (!l.price || !l.surface) continue;
      if (pc && l.postcode && l.postcode !== pc) continue;
      if (Math.abs(l.surface - surface) <= Math.max(1.5, 0.02 * surface) && Math.abs(l.price - price) / price <= 0.04) return { l, how: "même prix et surface" };
    }
  }
  return null;
}

async function verify() {
  const url = $("#v-url").value.trim(), price = num("#v-price"), surface = num("#v-surface"), pc = $("#v-pc").value.trim();
  if (!url && !(price && surface)) return toast("Collez un lien, ou indiquez prix + surface", true);
  const res = findListing(url, price, surface, pc);
  const box = res
    ? `<div class="result-box"><b class="ok">✅ Le radar connaît cette annonce</b> (${res.how}) — vue le ${fmtDate(res.l.first_seen)}, statut <b>${STATUS[res.l.status]}</b>.
        ${res.l.status === "rejete" ? `<br>Motif : ${esc(res.l.reasons.join(" · "))}` : ""}<br><a href="${esc(res.l.url)}" target="_blank">${esc(res.l.title || res.l.url)}</a></div>`
    : `<div class="result-box"><b class="err">❌ Le radar n'a pas vu cette annonce.</b><br>
        Si elle correspond à vos critères, c'est un trou de couverture : notez le site d'origine. S'il s'agit d'une agence, ajoutez-la dans Sources ›
        Sites d'agences, ou créez une alerte email sur ce portail vers l'adresse Gmail dédiée.</div>`;
  $("#v-result").innerHTML = box;
  try {
    const r = await updateJson("tracking.json", { items: {} }, (d) => {
      d.coverage_checks ||= [];
      d.coverage_checks.push({ date: new Date().toISOString(), url, price, surface, postcode: pc, found: !!res, status: res?.l.status || null, site: url ? new URL(url).hostname : null });
    }, "Contrôle de couverture");
    S.tracking = r.data; renderVerifyStats();
  } catch (e) { /* le résultat reste affiché même si l'enregistrement échoue */ }
}

function renderVerifyStats() {
  const checks = S.tracking.coverage_checks || [];
  if (!checks.length) { $("#v-stats").innerHTML = "<p class='muted'>Aucun contrôle pour l'instant.</p>"; return; }
  const found = checks.filter((c) => c.found).length;
  const bySite = {};
  checks.forEach((c) => { const k = c.site || "(sans lien)"; bySite[k] ||= [0, 0]; bySite[k][1]++; if (c.found) bySite[k][0]++; });
  $("#v-stats").innerHTML = `<p><b>${found} / ${checks.length}</b> annonces trouvées par le radar (${Math.round(100 * found / checks.length)} %).</p>
    <div class="table-wrap"><table><tr><th>Site</th><th>Trouvées</th></tr>${Object.entries(bySite).map(([k, [f, t]]) => `<tr><td>${esc(k)}</td><td class="${f === t ? "ok" : "warn"}">${f} / ${t}</td></tr>`).join("")}</table></div>`;
}

/* ---------------- Lancer le robot ---------------- */
async function runNow() {
  const btn = $("#btn-run");
  btn.disabled = true;
  try {
    await gh(`/repos/${conn().codeRepo}/actions/workflows/radar.yml/dispatches`, { method: "POST", body: JSON.stringify({ ref: "main" }) });
    toast("🚀 Recherche lancée. Résultats dans 3 à 10 minutes (rechargez la page).");
  } catch (e) {
    toast(e.status === 403 || e.status === 404 ? "Le jeton n'a pas le droit de lancer le robot (permission « Actions » sur le dépôt du code)." : "Échec : " + e.message, true);
  } finally { btn.disabled = false; }
}

/* ---------------- Démarrage ---------------- */
function showSetup() {
  const c = conn() || {};
  $("#set-data").value = c.dataRepo || ""; $("#set-code").value = c.codeRepo || ""; $("#set-token").value = c.token || "";
  if (!c.codeRepo && location.hostname.endsWith("github.io")) {
    const owner = location.hostname.split(".")[0], repo = location.pathname.split("/")[1];
    if (repo) { $("#set-code").value = `${owner}/${repo}`; $("#set-data").value = `${owner}/${repo}-data`; }
  }
  $("#setup").classList.remove("hidden");
  $$(".tab").forEach((t) => t.classList.add("hidden"));
}

async function start() {
  if (!conn()?.token) return showSetup();
  $("#setup").classList.add("hidden");
  showTab(localStorage.getItem("radar-tab") || "annonces");
  try { await loadAll(); } catch (e) {
    toast(e.status === 401 ? "Jeton GitHub invalide ou expiré." : e.status === 404 ? "Dépôt de données introuvable (nom ou droits du jeton)." : "Erreur de chargement : " + e.message, true);
    if (e.status === 401 || e.status === 404) showSetup();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  $$("#tabs button").forEach((b) => b.onclick = () => { if (conn()?.token) showTab(b.dataset.tab); });
  ["#f-track", "#f-sort", "#f-inactive"].forEach((s) => $(s).addEventListener("change", renderListings));
  $("#f-search").addEventListener("input", renderListings);
  $("#f-track").insertAdjacentHTML("beforeend", Object.entries(TRACK).map(([k, v]) => `<option value="${k}">${v}</option>`).join(""));
  $("#btn-settings").onclick = showSetup;
  $("#btn-save-setup").onclick = () => {
    const c = { dataRepo: $("#set-data").value.trim(), codeRepo: $("#set-code").value.trim(), token: $("#set-token").value.trim() };
    if (!c.dataRepo || !c.token) return toast("Dépôt des données et jeton obligatoires", true);
    try { localStorage.setItem("radar-conn", JSON.stringify(c)); } catch { return toast("Le navigateur refuse le stockage local", true); }
    start();
  };
  $("#btn-save-zone").onclick = saveZone;
  $("#btn-save-crit").onclick = saveCriteria;
  $("#btn-save-alerts").onclick = saveAlerts;
  $("#btn-add-agency").onclick = () => addAgencyRow({ name: "", enabled: true, list_urls: [], link_pattern: "", match_names: [], silence_alert_hours: 168 });
  $("#btn-save-agencies").onclick = saveAgencies;
  $("#btn-verify").onclick = verify;
  $("#btn-run").onclick = runNow;
  start();
});
