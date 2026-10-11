/* Liked Music, sorted by sound — dependency-free client-side search. */

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

function subsequence(needle, hay) {
  let i = 0;
  for (let j = 0; j < hay.length && i < needle.length; j++) {
    if (hay[j] === needle[i]) i++;
  }
  return i === needle.length;
}

let DATA = null;
let profiles = new Map();
let state = { q: "", cluster: null, shown: PAGE };

/* ── scoring ─────────────────────────────────────────────── */

function scoreTrack(t, q) {
  const tokens = q.split(" ").filter(Boolean);
  let s = 0;

  if (t._n === q) s += 120;
  else if (t._n.startsWith(q)) s += 70;
  else if (t._n.includes(q)) s += 45;
  else if (subsequence(q, t._n)) s += 12;

  if (t._a.startsWith(q)) s += 35;
  else if (t._a.includes(q)) s += 22;
  else if (subsequence(q, t._a)) s += 8;

  if (t._al.includes(q)) s += 8;
  if (t._c.includes(q)) s += 14;          // matches the genre name
  if (t._g.includes(q)) s += 10;          // matches styles / tags

  if (tokens.length > 1) {
    let hit = 0;
    for (const tk of tokens) {
      if (tk.length > 1 && t._all.includes(tk)) hit++;
    }
    s += (hit / tokens.length) * 30;
  }
  return s;
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
  const on = state.cluster === p.id;
  return `<button class="gcard" style="--h:${h}" data-cluster="${p.id}"
            aria-pressed="${on}" title="${esc(p.styles.join(', '))}">
      <div class="gname">${esc(p.name)}</div>
      <div class="gn">${p.n} songs${p.bpm ? ` · ${Math.round(p.bpm)} bpm` : ""}</div>
      <div class="gart">${esc(p.artists.slice(0, 3).join(", "))}</div>
    </button>`;
}

function renderGenres(matches) {
  const list = matches === null ? DATA.clusters : matches;
  $("genreGrid").innerHTML = list.map(genreCard).join("");
  $("genresHint").textContent = matches === null
    ? `${DATA.clusters.length} clusters — click one to filter the song list`
    : `${list.length} matching ${list.length === 1 ? "genre" : "genres"}`;
  $("genres").classList.toggle("hidden", list.length === 0);
}

function rowHTML(t) {
  const h = t.c >= 0 ? hue(t.cn) : 220;
  return `<div class="row" data-v="${esc(t.v)}">
    <div>
      <div class="t">${esc(t.t)}</div>
      <div class="meta">${esc(t.a)}${t.al ? " · " + esc(t.al) : ""}${t.y ? " · " + t.y : ""}</div>
    </div>
    <div class="bpm">${t.b ? Math.round(t.b) + " bpm" : ""}</div>
    <div class="chip${t.c < 0 ? " plain" : ""}" style="--h:${h}"
         data-cluster="${t.c}" title="Filter by this genre">${esc(t.cn)}</div>
  </div>`;
}

function detailHTML(t) {
  const p = profiles.get(t.c);
  const bits = [];
  if (p) {
    bits.push(`<span><span class="k">cluster:</span> ${esc(p.styles.slice(0, 4).join(", "))}</span>`);
    if (p.tags.length) bits.push(`<span><span class="k">tags:</span> ${esc(p.tags.slice(0, 5).join(", "))}</span>`);
    if (p.bpm) bits.push(`<span><span class="k">cluster bpm:</span> ${Math.round(p.bpm)}</span>`);
  }
  if (t.g && t.g.length) bits.push(`<span><span class="k">genres:</span> ${esc(t.g.join(", "))}</span>`);
  if (!t.m) bits.push(`<span><span class="k">no catalogue match — placed by tags</span></span>`);
  bits.push(`<a href="https://music.youtube.com/watch?v=${encodeURIComponent(t.v)}"
                 target="_blank" rel="noopener">Open in YouTube Music ↗</a>`);
  return `<div class="detail">${bits.join("")}</div>`;
}

function renderList() {
  const q = state.q;
  let rows = DATA.tracks;
  if (state.cluster !== null) rows = rows.filter((t) => t.c === state.cluster);
  if (q) {
    const scored = [];
    for (const t of rows) {
      const s = scoreTrack(t, q);
      if (s > 0) scored.push([s, t]);
    }
    scored.sort((a, b) => b[0] - a[0]);
    rows = scored.map((x) => x[1]);
  }

  const title = state.cluster !== null
    ? (profiles.get(state.cluster)?.name || "Songs")
    : (q ? "Results" : "All songs");
  $("songsTitle").textContent = `${title} · ${rows.length.toLocaleString()}`;

  $("filters").innerHTML = state.cluster !== null
    ? `<span class="pill">${esc(profiles.get(state.cluster)?.name || "")}
         <button type="button" data-clear="1" aria-label="Clear genre filter">×</button></span>`
    : "";

  if (!rows.length) {
    $("list").innerHTML = `<div class="empty"><b>No match for “${esc(q)}”</b>
      Try an artist, a track name, or a genre like <em>shoegaze</em>.</div>`;
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
  const q = norm(state.q);
  const matches = q ? DATA.clusters
    .map((p) => [scoreGenre(p, q), p])
    .filter(([s]) => s > 0)
    .sort((a, b) => b[0] - a[0])
    .map((x) => x[1]) : null;
  renderGenres(matches);
  renderList();
}

/* ── URL state ───────────────────────────────────────────── */

function syncURL() {
  const url = new URL(location.href);
  if (state.q) url.searchParams.set("q", state.q); else url.searchParams.delete("q");
  if (state.cluster !== null) url.searchParams.set("genre", state.cluster);
  else url.searchParams.delete("genre");
  history.replaceState(null, "", url);
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

document.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip[data-cluster]");
  if (chip) {
    const id = Number(chip.dataset.cluster);
    state.cluster = state.cluster === id ? null : id;
    state.shown = PAGE;
    syncURL();
    render();
    $("songsTitle").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  const card = e.target.closest(".gcard");
  if (card) {
    const id = Number(card.dataset.cluster);
    state.cluster = state.cluster === id ? null : id;
    state.shown = PAGE;
    syncURL();
    render();
    $("songsTitle").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (e.target.closest("[data-clear]")) {
    state.cluster = null;
    state.shown = PAGE;
    syncURL();
    render();
    return;
  }
  if (e.target.closest("[data-more]")) {
    state.shown += PAGE;
    renderList();
    return;
  }
  const row = e.target.closest(".row");
  if (row && !e.target.closest("a")) {
    const open = row.querySelector(".detail");
    if (open) { open.remove(); return; }
    const t = DATA.tracks.find((x) => x.v === row.dataset.v);
    if (t) row.insertAdjacentHTML("beforeend", detailHTML(t));
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement !== $("q")) {
    e.preventDefault();
    $("q").focus();
    $("q").select();
  } else if (e.key === "Escape") {
    state.q = "";
    state.cluster = null;
    $("q").value = "";
    $("q").blur();
    syncURL();
    render();
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
      profiles.set(p.id, p);
    }
    for (const t of DATA.tracks) {
      t._n = norm(t.t);
      t._a = norm(t.a);
      t._al = norm(t.al);
      t._c = norm(t.cn);
      t._g = norm([...(t.g || []), ...(profiles.get(t.c)?.styles || [])].join(" "));
      t._all = `${t._n} ${t._a} ${t._al} ${t._c} ${t._g}`;
    }
    const params = new URLSearchParams(location.search);
    state.q = params.get("q") || "";
    const g = params.get("genre");
    state.cluster = g !== null && g !== "" && profiles.has(Number(g)) ? Number(g) : null;
    $("q").value = state.q;
    renderStats();
    render();
  })
  .catch((err) => {
    $("sub").textContent = "Could not load data.json";
    $("list").innerHTML = `<div class="empty"><b>Data failed to load</b>${esc(err.message)}</div>`;
  });
