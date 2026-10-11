/* Liked Music, sorted by sound — dependency-free client-side search.
 *
 * Two views, routed through the query string so refresh and Back both work:
 *   home                 ?q=…&cluster=…&tag=…
 *   one genre's page     ?view=genre&id=…   (tags filter within that genre)
 *
 * Filter model: clusters are OR'd within their facet, tags/genres are OR'd
 * within theirs, and the facets AND together with the free-text query.
 * Tags are derived from a song's cluster, so requiring all of them (AND) would
 * almost always return nothing — "shoegaze" and "boom bap" never co-occur.
 */

const PAGE = 150;

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const norm = (s) => String(s || "").toLowerCase().normalize("NFD")
  .replace(/[\u0300-\u036f]/g, "").replace(/[^a-z0-9]+/g, " ").trim();

/* Stable colour per genre name, so a cluster looks the same everywhere. */
function hue(name) {
  let h = 2166136261;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h % 360;
}

/* Bounded fuzzy match: the query's letters must appear within a short window of
 * the target AND at the start of a word. That tolerates a typo
 * ("shogaze" → "shoegaze") but rejects the false positives an unbounded
 * subsequence produces — e.g. "laika" matching "Edward Maya feat. Jigulina
 * Vika", where l-a-i-k-a happen to sit inside the surname "jigulina vika". */
function fuzzyWindow(needle, hay) {
  let i = 0;
  let end = -1;
  for (let j = 0; j < hay.length && i < needle.length; j++) {
    if (hay[j] === needle[i]) { i++; end = j; }
  }
  if (i < needle.length) return null;
  let k = needle.length - 1;
  let start = end;
  for (let j = end; j >= 0 && k >= 0; j--) {
    if (hay[j] === needle[k]) { if (k === 0) start = j; k--; }
  }
  return { start, span: end - start + 1 };
}

const FUZZ_MIN = 4;   // shorter queries match too much to fuzz safely
const FUZZ_SLACK = 2; // allowed extra characters inside the matched window

function fuzzScore(q, field) {
  if (q.length < FUZZ_MIN) return 0;
  const w = fuzzyWindow(q, field);
  if (!w || w.span > q.length + FUZZ_SLACK) return 0;
  if (w.start > 0 && field[w.start - 1] !== " ") return 0; // must begin a word
  return 1;
}

let DATA = null;
let profiles = new Map();

const state = {
  page: { kind: "home" },  // or { kind: "genre", id }
  q: "",
  clusters: new Set(),     // cluster ids, OR'd
  tags: new Map(),         // normalized key -> display label, OR'd
  expanded: new Set(),     // videoIds whose detail panel is open
  shown: PAGE,
  cameFromHome: false,
};

const onGenrePage = () => state.page.kind === "genre";

/* ── scoring ─────────────────────────────────────────────── */

function scoreTrack(t, q) {
  const tokens = q.split(" ").filter(Boolean);
  let s = 0;
  let strong = false;   // a result only appears if it has a genuine match

  if (t._n === q) { s += 120; strong = true; }
  else if (t._n.startsWith(q)) { s += 70; strong = true; }
  else if (t._n.includes(q)) { s += 45; strong = true; }

  if (t._a.startsWith(q)) { s += 35; strong = true; }
  else if (t._a.includes(q)) { s += 22; strong = true; }

  if (t._al.includes(q)) { s += 8; strong = true; }
  if (t._c.includes(q)) { s += 14; strong = true; }   // matches the genre name
  if (t._g.includes(q)) { s += 10; strong = true; }   // matches styles / tags

  if (tokens.length > 1) {
    let hit = 0;
    for (const tk of tokens) {
      if (tk.length > 1 && t._all.includes(tk)) hit++;
    }
    s += (hit / tokens.length) * 30;
    if (hit === tokens.length) strong = true;
  }

  if (!strong) {
    if (fuzzScore(q, t._n)) { s += 10; strong = true; }
    else if (fuzzScore(q, t._a)) { s += 6; strong = true; }
  }
  return strong ? s : 0;
}

function scoreGenre(p, q) {
  let s = 0;
  if (p._name === q) s += 100;
  else if (p._name.startsWith(q)) s += 60;
  else if (p._name.includes(q)) s += 40;
  if (p._text.includes(q)) s += 22;
  for (const tk of q.split(" ").filter((x) => x.length > 1)) {
    if (p._text.includes(tk)) s += 8;
  }
  return s;
}

/* ── filtering ───────────────────────────────────────────── */

function passesFilters(t) {
  if (onGenrePage() && t.c !== state.page.id) return false;
  if (state.clusters.size && !state.clusters.has(t.c)) return false;
  if (state.tags.size) {
    let any = false;
    for (const key of state.tags.keys()) {
      if (t._tags.has(key)) { any = true; break; }
    }
    if (!any) return false;
  }
  return true;
}

function matchingClusters() {
  let list = DATA.clusters;
  if (state.tags.size) {
    list = list.filter((p) => {
      for (const key of state.tags.keys()) if (p._tags.has(key)) return true;
      return false;
    });
  }
  if (state.q) {
    const q = norm(state.q);
    list = list
      .map((p) => [scoreGenre(p, q), p])
      .filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0])
      .map((x) => x[1]);
  }
  return list;
}

/* ── rendering ───────────────────────────────────────────── */

function renderStats() {
  const s = DATA.stats;
  const pct = Math.round((s.clustered / s.songs) * 100);
  $("sub").textContent =
    `${s.songs.toLocaleString()} liked songs → ${s.clusters} genres, from 30-second audio previews`;
  $("footnote").textContent = `Index built ${DATA.generated}.`;
  $("stats").innerHTML = [
    [s.songs.toLocaleString(), "songs in the library"],
    [s.clusters, "genre clusters"],
    [s.clustered.toLocaleString(), `clustered by sound (${pct}%)`],
    [(s.songs - s.clustered).toLocaleString(), "left unclustered"],
  ].map(([b, span]) => `<div class="stat"><b>${b}</b><span>${span}</span></div>`).join("");
}

function genreCard(p) {
  const h = hue(p.name);
  const added = state.clusters.has(p.id);
  return `<div class="gcard" style="--h:${h}">
    <button class="gopen" type="button" data-open="${p.id}" title="Open this genre">
      <div class="gname">${esc(p.name)}</div>
      <div class="gn">${p.n} songs${p.bpm ? ` · ${Math.round(p.bpm)} bpm` : ""}</div>
      <div class="gart">${esc(p.artists.slice(0, 3).join(", "))}</div>
    </button>
    <button class="gadd${added ? " on" : ""}" type="button" data-add="${p.id}"
            aria-pressed="${added}"
            title="${added ? "Remove from filter" : "Add to filter"}">${added ? "✓" : "+"}</button>
  </div>`;
}

function renderGenres() {
  const list = matchingClusters();
  $("genreGrid").innerHTML = list.map(genreCard).join("");
  const bits = [`${list.length} ${list.length === 1 ? "cluster" : "clusters"}`];
  if (state.clusters.size) bits.push(`${state.clusters.size} in filter`);
  if (state.tags.size) bits.push(`filtered by ${state.tags.size} tag${state.tags.size === 1 ? "" : "s"}`);
  $("genresHint").textContent = bits.join(" · ") + " — open one, or ＋ to filter";
  $("genres").classList.toggle("hidden", list.length === 0);
}

function tagBtn(label, kind, value) {
  const on = kind === "cluster"
    ? state.clusters.has(Number(value))
    : state.tags.has(norm(label));
  return `<button class="tag${on ? " on" : ""}" type="button"
      data-${kind}="${esc(value ?? label)}" aria-pressed="${on}">${esc(label)}</button>`;
}

function rowHTML(t) {
  const h = t.c >= 0 ? hue(t.cn) : 220;
  const open = state.expanded.has(t.v);
  const chip = onGenrePage() ? "" :
    `<div class="chip${t.c < 0 ? " plain" : ""}" style="--h:${h}" data-open="${t.c}"
          title="Open this genre">${esc(t.cn)}</div>`;
  return `<div class="row" data-v="${esc(t.v)}">
    <div>
      <div class="t">${esc(t.t)}</div>
      <div class="meta">${esc(t.a)}${t.al ? " · " + esc(t.al) : ""}${t.y ? " · " + t.y : ""}</div>
    </div>
    <div class="bpm">${t.b ? Math.round(t.b) + " bpm" : ""}</div>
    ${chip}
    ${open ? detailHTML(t) : ""}
  </div>`;
}

function detailHTML(t) {
  const p = profiles.get(t.c);
  const bits = [];
  if (p) {
    bits.push(`<span class="line"><span class="k">cluster</span>${tagBtn(p.name, "cluster", p.id)}</span>`);
    bits.push(`<span class="line"><span class="k">styles</span>${p.styles.slice(0, 5).map((s) => tagBtn(s, "tag")).join("")}</span>`);
    if (p.tags.length) {
      bits.push(`<span class="line"><span class="k">tags</span>${p.tags.slice(0, 6).map((s) => tagBtn(s, "tag")).join("")}</span>`);
    }
  }
  if (t.g && t.g.length) {
    bits.push(`<span class="line"><span class="k">genres</span>${t.g.map((s) => tagBtn(s, "tag")).join("")}</span>`);
  }
  const extra = [];
  if (t.b) extra.push(`${Math.round(t.b)} bpm`);
  if (p && p.bpm) extra.push(`cluster avg ${Math.round(p.bpm)} bpm`);
  if (!t.m) extra.push("no catalogue match — placed by tags");
  extra.push(`<a href="https://music.youtube.com/watch?v=${encodeURIComponent(t.v)}"
                 target="_blank" rel="noopener">Open in YouTube Music ↗</a>`);
  bits.push(`<span class="line dim">${extra.join(" · ")}</span>`);
  return `<div class="detail">${bits.join("")}</div>`;
}

function filterBar() {
  const pills = [];
  for (const id of state.clusters) {
    const p = profiles.get(id);
    if (p) pills.push(`<span class="pill" style="--h:${hue(p.name)}">
      <span class="dot"></span>${esc(p.name)}
      <button type="button" data-drop-cluster="${id}" aria-label="Remove ${esc(p.name)}">×</button></span>`);
  }
  for (const [key, label] of state.tags) {
    pills.push(`<span class="pill tagpill">#${esc(label)}
      <button type="button" data-drop-tag="${esc(key)}" aria-label="Remove ${esc(label)}">×</button></span>`);
  }
  if (pills.length > 1) {
    pills.push(`<button class="clearall" type="button" data-clear-all="1">clear all</button>`);
  }
  $("filters").innerHTML = pills.join("");
}

function renderGenrePage(p) {
  if (!p) return;
  $("gpTitle").textContent = p.name;
  const meta = [`${p.n} songs`];
  if (p.bpm) meta.push(`${Math.round(p.bpm)} bpm avg`);
  if ((p.years || []).length) meta.push(`mostly ${p.years.join(", ")}`);
  if ((p.artists || []).length) meta.push(p.artists.slice(0, 4).join(", "));
  $("gpMeta").textContent = meta.join(" · ");
  const styles = (p.styles || []).slice(0, 5);
  const tags = (p.tags || []).slice(0, 8);
  $("gpChips").innerHTML =
    `<span class="line"><span class="k">styles</span>${styles.map((s) => tagBtn(s, "tag")).join("")}</span>` +
    (tags.length
      ? `<span class="line"><span class="k">tags</span>${tags.map((s) => tagBtn(s, "tag")).join("")}</span>`
      : "");
}

function renderList() {
  const q = norm(state.q);
  let rows = DATA.tracks.filter(passesFilters);
  if (q) {
    const scored = [];
    for (const t of rows) {
      const s = scoreTrack(t, q);
      if (s > 0) scored.push([s, t]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    rows = scored.map((x) => x[1]);
  }

  const parts = [];
  if (onGenrePage()) {
    const p = profiles.get(state.page.id);
    if (p) parts.push(`in ${p.name}`);
  } else if (state.clusters.size === 1) {
    parts.push(profiles.get([...state.clusters][0])?.name || "Cluster");
  } else if (state.clusters.size > 1) {
    parts.push(`${state.clusters.size} clusters`);
  }
  if (state.tags.size) parts.push(`${state.tags.size} tag${state.tags.size === 1 ? "" : "s"}`);
  if (q) parts.push(`“${state.q}”`);
  $("songsTitle").textContent =
    `${parts.length ? parts.join(" · ") : "All songs"} · ${rows.length.toLocaleString()}`;

  filterBar();

  if (!rows.length) {
    const hint = state.tags.size || state.clusters.size
      ? "No song matches every filter. Remove one, or clear all."
      : `It may not be in this library — try another spelling, an artist, album or genre
         (like <em>shoegaze</em>).`;
    $("list").innerHTML = `<div class="empty"><b>No match${
      q ? ` for “${esc(state.q)}”` : ""}</b>${hint}</div>`;
    $("more").innerHTML = "";
    return;
  }

  $("list").innerHTML = rows.slice(0, state.shown).map(rowHTML).join("");
  const left = rows.length - state.shown;
  $("more").innerHTML = left > 0
    ? `<button type="button" data-more="1">Show ${Math.min(left, PAGE)} more (${left.toLocaleString()} left)</button>`
    : "";
}

function render() {
  const genre = onGenrePage();
  $("stats").classList.toggle("hidden", genre);
  $("genrePage").classList.toggle("hidden", !genre);
  $("genres").classList.toggle("hidden", genre);
  if (genre) renderGenrePage(profiles.get(state.page.id));
  else renderGenres();
  renderList();
}

/* ── routing ─────────────────────────────────────────────── */

function readURL() {
  const params = new URLSearchParams(location.search);
  state.q = params.get("q") || "";
  state.clusters.clear();
  for (const id of (params.get("cluster") || "").split(",")) {
    const n = Number(id);
    if (id !== "" && profiles.has(n)) state.clusters.add(n);
  }
  state.tags.clear();
  for (const label of (params.get("tag") || "").split(",")) {
    const key = norm(label);
    if (key) state.tags.set(key, label.trim());
  }
  const id = Number(params.get("id"));
  state.page = params.get("view") === "genre" && profiles.has(id)
    ? { kind: "genre", id }
    : { kind: "home" };
  state.shown = PAGE;
  $("q").value = state.q;
}

function syncURL(push = false) {
  const url = new URL(location.href);
  const set = (k, v) => { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); };
  set("q", state.q);
  set("cluster", [...state.clusters].join(","));
  set("tag", [...state.tags.values()].join(","));
  if (onGenrePage()) { set("view", "genre"); set("id", state.page.id); }
  else { set("view", ""); set("id", ""); }
  (push ? history.pushState : history.replaceState).call(history, null, "", url);
}

function openGenre(id) {
  if (!profiles.has(id)) return;
  state.page = { kind: "genre", id };
  state.clusters.clear();
  state.tags.clear();
  state.expanded.clear();
  state.cameFromHome = true;
  syncURL(true);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function goHome() {
  if (state.cameFromHome) {          // restore the previous view from history
    state.cameFromHome = false;
    history.back();
    return;
  }
  state.page = { kind: "home" };
  syncURL(true);
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

/* ── state changes ───────────────────────────────────────── */

function toggleCluster(id) {
  if (state.clusters.has(id)) state.clusters.delete(id);
  else state.clusters.add(id);
  state.shown = PAGE;
  syncURL();
  render();
}

function toggleTag(label) {
  const key = norm(label);
  if (!key) return;
  if (state.tags.has(key)) state.tags.delete(key);
  else state.tags.set(key, label);
  state.shown = PAGE;
  syncURL();
  render();
}

function clearAll() {
  state.clusters.clear();
  state.tags.clear();
  state.q = "";
  $("q").value = "";
  state.shown = PAGE;
  syncURL();
  render();
}

function revealList() {
  $("songsTitle").scrollIntoView({ behavior: "smooth", block: "center" });
}

/* ── events ──────────────────────────────────────────────── */

let timer;
$("q").addEventListener("input", (e) => {
  clearTimeout(timer);
  const v = e.target.value;
  timer = setTimeout(() => {
    state.q = v;
    state.shown = PAGE;
    syncURL();
    render();
  }, 90);
});

window.addEventListener("popstate", () => {
  state.cameFromHome = false;
  readURL();
  render();
});

document.addEventListener("click", (e) => {
  if (e.target.closest("[data-home]")) { goHome(); return; }

  const tag = e.target.closest("[data-tag]");
  if (tag) { toggleTag(tag.dataset.tag); revealList(); return; }

  const open = e.target.closest("[data-open]");
  if (open) { openGenre(Number(open.dataset.open)); return; }

  const add = e.target.closest("[data-add]");
  if (add) { toggleCluster(Number(add.dataset.add)); return; }

  const cbtn = e.target.closest(".tag[data-cluster]");
  if (cbtn) { openGenre(Number(cbtn.dataset.cluster)); return; }

  const dropC = e.target.closest("[data-drop-cluster]");
  if (dropC) { toggleCluster(Number(dropC.dataset.dropCluster)); return; }

  const dropT = e.target.closest("[data-drop-tag]");
  if (dropT) { state.tags.delete(dropT.dataset.dropTag); state.shown = PAGE; syncURL(); render(); return; }

  if (e.target.closest("[data-clear-all]")) { clearAll(); return; }
  if (e.target.closest("[data-more]")) { state.shown += PAGE; renderList(); return; }

  const row = e.target.closest(".row");
  if (row && !e.target.closest("a") && !e.target.closest("button")) {
    const t = DATA.tracks.find((x) => x.v === row.dataset.v);
    if (!t) return;
    if (state.expanded.has(t.v)) state.expanded.delete(t.v);
    else state.expanded.add(t.v);
    // replace only this row: a full re-render would drop scroll position and
    // close any other open panels
    row.outerHTML = rowHTML(t);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("q")) {
    e.preventDefault();
    $("q").focus();
    $("q").select();
  } else if (e.key === "Escape") {
    clearAll();
    $("q").blur();
  }
});

/* ── boot ────────────────────────────────────────────────── */

fetch("data.json")
  .then((r) => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
  .then((data) => {
    DATA = data;
    for (const p of DATA.clusters) {
      p._name = norm(p.name);
      p._text = norm([p.name, ...p.styles, ...p.tags, ...p.artists].join(" "));
      p._tags = new Set([...p.styles, ...p.tags, p.name].map(norm).filter(Boolean));
      profiles.set(p.id, p);
    }
    for (const t of DATA.tracks) {
      t._n = norm(t.t);
      t._a = norm(t.a);
      t._al = norm(t.al);
      t._c = norm(t.cn);
      t._g = norm([...(t.g || []), ...(profiles.get(t.c)?.styles || [])].join(" "));
      t._all = `${t._n} ${t._a} ${t._al} ${t._c} ${t._g}`;
      t._tags = profiles.get(t.c)?._tags || new Set();
    }
    readURL();
    renderStats();
    render();
  })
  .catch((err) => {
    $("sub").textContent = "Could not load data.json";
    $("list").innerHTML = `<div class="empty"><b>Data failed to load</b>${esc(err.message)}</div>`;
  });
