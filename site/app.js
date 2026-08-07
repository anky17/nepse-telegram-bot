// Proxied through the Worker (worker/index.js `/data/*`), which caches every
// successful upstream response in KV and serves the last-known-good copy if
// the upstream (omitnomis.github.io/ShareSansarScraper) deploy is ever broken.
const API = "https://nepse-telegram-bot.ankitdev.workers.dev/data/";
const FLOORSHEET_BASE = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/floorsheet";
const DIVIDEND_BASE = "https://raw.githubusercontent.com/SamirWagle/Nepse-All-Scraper/main/data/company-wise";

// Independently-fetched widgets (index badge, sector strip, notices, etc.)
// render as soon as their own data arrives, but shouldn't pop into view
// ahead of the main boot spinner — that reads as a broken, half-loaded page.
// They await this before revealing themselves, so everything appears in one
// consistent moment once boot() finishes, no matter which fetch wins the race.
let resolveBootDone;
const bootDone = new Promise((resolve) => { resolveBootDone = resolve; });

const state = {
  latestRows: [],       // this session's rows, keyed lookup below
  rowsBySymbol: new Map(),
  sortKey: "turnover",
  sortDir: -1,
  historyCache: new Map(),
  range: 0,              // days; 0 = all
  currentSymbol: null,
  floorsheetByDate: new Map(), // date -> { date, rows }
  companyNames: new Map(), // symbol -> full company name
  watchlist: loadWatchlist(),
  indicators: { sma20: false, sma50: false, rsi: false },
  boardFilter: "all",   // all | near-high | near-low
  sectorMap: null,      // symbol -> sector name
  sectorPerf: [],        // [{name, pct}] today's sector performance
  bullRatio: 0,
};

function loadWatchlist() {
  try {
    return new Set(JSON.parse(localStorage.getItem("ghantaghar:watchlist") || "[]"));
  } catch {
    return new Set();
  }
}

function saveWatchlist() {
  localStorage.setItem("ghantaghar:watchlist", JSON.stringify([...state.watchlist]));
}

function toggleWatch(symbol) {
  if (state.watchlist.has(symbol)) state.watchlist.delete(symbol);
  else state.watchlist.add(symbol);
  saveWatchlist();
  renderWatchlist();
  renderBoard();
  if (symbol === state.currentSymbol) updateDetailStar();
}

const $ = (id) => document.getElementById(id);
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const MIN_BOOT_MS = 2000;

function fmtNum(n, digits = 2) {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return Number(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function fmtCompact(n) {
  if (n === null || n === undefined) return "—";
  const abs = Math.abs(n);
  if (abs >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (abs >= 1e6) return (n / 1e6).toFixed(2) + "M";
  if (abs >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(n);
}

function fmtPct(n) {
  if (n === null || n === undefined) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${Number(n).toFixed(2)}%`;
}

function clockTick() {
  const now = new Date();
  const npt = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kathmandu",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }).format(now);
  $("clock").textContent = `${npt} NPT`;
}
setInterval(clockTick, 1000);
clockTick();

async function loadIndex() {
  try {
    const idx = await fetchJSON("index.json");
    if (idx.error) throw new Error(idx.error);
    const cls = idx.change >= 0 ? "up" : "down";
    const arrow = idx.change >= 0 ? "▲" : "▼";
    const chgText = `${arrow} ${fmtNum(Math.abs(idx.change))} (${fmtPct(idx.pct)})`;

    $("indexValue").textContent = fmtNum(idx.current);
    const chgEl = $("indexChg");
    chgEl.textContent = chgText;
    chgEl.className = `index-chg ${cls}`;
    await bootDone;
    $("indexBadge").classList.remove("hidden");

    $("heroIndexValue").textContent = fmtNum(idx.current);
    const heroChgEl = $("heroIndexChg");
    heroChgEl.textContent = chgText;
    heroChgEl.className = `index-hero-chg ${cls}`;
    $("heroIndexRange").textContent = `H: ${fmtNum(idx.high)}   L: ${fmtNum(idx.low)}`;
  } catch (err) {
    console.error("index fetch failed", err);
  }
}
loadIndex();

async function loadIndexIntraday() {
  try {
    const data = await fetchJSON("index-intraday.json");
    if (data.error) throw new Error(data.error);
    const points = (data.points || []).filter((p) => typeof p[1] === "number");
    if (points.length < 2) return;

    const w = 300, h = 90;
    const values = points.map((p) => p[1]);
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    const stepX = w / (points.length - 1);
    const coords = values.map((v, i) => [i * stepX, h - ((v - min) / span) * h]);
    const linePath = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
    const fillPath = `${linePath} L${w},${h} L0,${h} Z`;
    const cls = values[values.length - 1] >= values[0] ? "up" : "down";

    const svg = $("indexHeroChart");
    svg.innerHTML =
      `<path class="spark-fill ${cls}" d="${fillPath}"></path>` +
      `<path class="spark-line ${cls}" d="${linePath}"></path>` +
      `<line class="spark-guide hidden" id="sparkGuide" x1="0" y1="0" x2="0" y2="${h}"></line>` +
      `<circle class="spark-dot ${cls} hidden" id="sparkDot" r="3"></circle>`;
    svg.classList.remove("hidden");

    heroChart = { points, coords, w, h, cls };
    attachHeroChartCrosshair();
  } catch (err) {
    console.error("index intraday fetch failed", err);
  }
}
loadIndexIntraday();

let heroChart = null;

function attachHeroChartCrosshair() {
  const svg = $("indexHeroChart");
  const tooltip = $("indexHeroTooltip");
  const guide = $("sparkGuide");
  const dot = $("sparkDot");

  svg.onmousemove = (e) => {
    if (!heroChart) return;
    const rect = svg.getBoundingClientRect();
    const ratio = (e.clientX - rect.left) / rect.width;
    const i = Math.max(0, Math.min(heroChart.points.length - 1, Math.round(ratio * (heroChart.points.length - 1))));
    const [x, y] = heroChart.coords[i];
    const [ts, val] = heroChart.points[i];

    guide.setAttribute("x1", x); guide.setAttribute("x2", x);
    guide.classList.remove("hidden");
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
    dot.classList.remove("hidden");

    const time = new Date(ts * 1000).toLocaleTimeString("en-US", {
      timeZone: "Asia/Kathmandu", hour: "2-digit", minute: "2-digit",
    });
    tooltip.innerHTML = `<span class="th-time">${time}</span><span class="th-val">${fmtNum(val)}</span>`;
    tooltip.style.left = `${(x / heroChart.w) * rect.width}px`;
    tooltip.classList.remove("hidden");
  };
  svg.onmouseleave = () => {
    guide.classList.add("hidden");
    dot.classList.add("hidden");
    tooltip.classList.add("hidden");
  };
}

async function loadSectors() {
  try {
    const data = await fetchJSON("sectors.json");
    if (data.error) throw new Error(data.error);
    state.sectorPerf = data.sectors || [];
    const row = $("sectorStripRow");
    row.innerHTML = state.sectorPerf.map((s) => {
      const cls = s.pct >= 0 ? "up" : "down";
      return `<span class="sector-chip"><span class="sc-name">${s.name}</span><span class="sc-pct ${cls}">${fmtPct(s.pct)}</span></span>`;
    }).join("");
    $("sectorStripDate").textContent = data.date || "";
    await bootDone;
    $("sectorStrip").classList.toggle("hidden", !state.sectorPerf.length);
  } catch (err) {
    console.error("sectors fetch failed", err);
  }
}
loadSectors();

async function loadSectorMap() {
  try {
    const data = await fetchJSON("sector-map.json");
    if (data.error) throw new Error(data.error);
    state.sectorMap = data;
    if (state.currentSymbol) renderSectorTag(state.currentSymbol);
  } catch (err) {
    console.error("sector map fetch failed", err);
  }
}
loadSectorMap();

async function loadCircuits() {
  try {
    const data = await fetchJSON("circuits.json");
    if (data.error) throw new Error(data.error);
    const items = data.items || [];
    if (!items.length) return;
    const row = $("circuitStripRow");
    row.innerHTML = items.map((c) => {
      const arrow = c.direction === "up" ? "▲" : "▼";
      return `<span class="circuit-chip ${c.direction}">${arrow} <b>${c.symbol}</b> ${fmtPct(c.pct)} <span class="cc-time">${c.time || ""}</span></span>`;
    }).join("");
    await bootDone;
    $("circuitStrip").classList.remove("hidden");
  } catch (err) {
    console.error("circuits fetch failed", err);
  }
}
loadCircuits();

async function loadNotices() {
  try {
    const data = await fetchJSON("notices.json");
    if (data.error) throw new Error(data.error);
    const items = data.items || [];
    if (!items.length) return;
    const list = $("noticesList");
    list.innerHTML = items.map((n) => `
      <li class="notices-item ${n.important ? "important" : ""}">
        ${n.important ? `<span class="ni-tag">KEY</span>` : ""}
        <span class="ni-title">${n.title}</span>
      </li>`).join("");
    $("noticesToggle").classList.toggle("hidden", items.length <= 3);
    await bootDone;
    $("noticesSection").classList.remove("hidden");
  } catch (err) {
    console.error("notices fetch failed", err);
  }
}
loadNotices();

$("noticesToggle").addEventListener("click", () => {
  const list = $("noticesList");
  const collapsed = list.classList.toggle("collapsed");
  $("noticesToggle").textContent = collapsed ? "Show all ▾" : "Show fewer ▴";
});

$("pulseTabs").addEventListener("click", (e) => {
  const btn = e.target.closest(".pulse-tab");
  if (!btn) return;
  const view = btn.dataset.view;
  $("pulseTabs").querySelectorAll(".pulse-tab").forEach((b) => b.classList.toggle("active", b === btn));
  $("chartView").classList.toggle("hidden", view !== "chart");
  $("pulseMood").classList.toggle("hidden", view !== "meter");
});

async function fetchJSON(path) {
  const r = await fetch(API + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return r.json();
}

async function loadCompanyNames() {
  try {
    const r = await fetch("companies.json", { cache: "no-store" });
    if (!r.ok) return;
    const json = await r.json();
    state.companyNames = new Map(Object.entries(json));
  } catch {
    // full-name search just degrades to symbol-only search
  }
}

async function boot() {
  const isFirstVisit = !localStorage.getItem("ghantaghar:visited");
  localStorage.setItem("ghantaghar:visited", "1");

  const started = Date.now();
  const waitOutMinBoot = () => {
    if (!isFirstVisit) return Promise.resolve();
    const remaining = MIN_BOOT_MS - (Date.now() - started);
    return remaining > 0 ? sleep(remaining) : Promise.resolve();
  };

  try {
    const [meta, recap, latest] = await Promise.all([
      fetchJSON("meta.json"),
      fetchJSON("recap/latest.json"),
      fetchJSON("latest.json"),
      loadCompanyNames(),
    ]);

    $("archiveStat").textContent = `${meta.tradingDays} sessions archived since ${meta.firstDate}`;

    state.latestRows = latest.rows || [];
    for (const row of state.latestRows) state.rowsBySymbol.set(row.symbol, row);

    renderPulse(recap);
    renderMovers();
    renderMostActive();
    renderBoard();
    renderWatchlist();
    renderTicker();

    await waitOutMinBoot();

    $("boot").classList.add("hidden");
    $("pulse").classList.remove("hidden");
    $("movers").classList.remove("hidden");
    $("board").classList.remove("hidden");
    $("bulkSection").classList.remove("hidden");
    animateMoodNeedle(state.bullRatio);
    resolveBootDone();

    const hashSym = location.hash.replace("#", "").toUpperCase();
    if (hashSym && state.rowsBySymbol.has(hashSym)) openDetail(hashSym);
  } catch (err) {
    console.error(err);
    await waitOutMinBoot();
    $("boot").classList.add("hidden");
    $("feedError").classList.remove("hidden");
    resolveBootDone();
  }
}

// Gauge arc geometry: center (60,62) r=46, sweeping the top semicircle from
// f=0 (leftmost, angle 180°) to f=1 (rightmost, angle 0°) through the top
// (f=0.5, angle 90°). Mirrors the fixed radius used by the static SVG paths.
function arcPoint(f, r) {
  const rad = (180 - f * 180) * Math.PI / 180;
  return [60 + r * Math.cos(rad), 62 - r * Math.sin(rad)];
}

function gaugeArcPath(f0, f1) {
  const [x0, y0] = arcPoint(f0, 46);
  const [x1, y1] = arcPoint(f1, 46);
  const largeArc = (f1 - f0) > 0.5 ? 1 : 0;
  return `M${x0.toFixed(2)},${y0.toFixed(2)} A46,46 0 ${largeArc} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
}

// Sets the needle/fill-arc to their resting (most-bearish/empty) state with
// transitions disabled, so a later animateMoodNeedle() call always has a real
// "before" frame to sweep from — setting a target value while #pulse is still
// display:none (as it is during boot()) paints instantly with no animation.
function resetMoodNeedle() {
  const needle = $("moodNeedle");
  needle.style.transition = "none";
  needle.style.transform = "rotate(-90deg)";
  const fill = $("gaugeFill");
  fill.style.transition = "none";
  fill.setAttribute("d", gaugeArcPath(0, 0));
}

function animateMoodNeedle(bullRatio) {
  const needle = $("moodNeedle");
  const fill = $("gaugeFill");
  void needle.getBoundingClientRect(); // force the resting frame to commit
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      needle.style.transition = "";
      needle.style.transform = `rotate(${bullRatio * 180 - 90}deg)`;
      fill.style.transition = "";
      fill.setAttribute("d", gaugeArcPath(0, bullRatio));
    });
  });
}

function renderPulse(recap) {
  const advances = recap.advances || 0;
  const declines = recap.declines || 0;
  const unchanged = recap.unchanged || 0;
  const total = advances + declines + unchanged;
  const bullRatio = (advances + declines) ? advances / (advances + declines) : 0;

  let mood, cls;
  if (bullRatio >= 0.65) { mood = "BROADLY BULLISH"; cls = "bull"; }
  else if (bullRatio >= 0.45) { mood = "MIXED"; cls = "mixed"; }
  else { mood = "BROADLY BEARISH"; cls = "bear"; }

  $("moodGauge").classList.remove("bull", "mixed", "bear");
  $("moodGauge").classList.add(cls);
  $("pulseMood").classList.remove("bull", "mixed", "bear");
  $("pulseMood").classList.add(cls);
  resetMoodNeedle();
  state.bullRatio = bullRatio;
  $("moodScore").textContent = Math.round(bullRatio * 100);
  $("moodLabel").textContent = mood;
  $("pulseDate").textContent = recap.date || "—";

  const todayNPT = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kathmandu" }).format(new Date());
  $("staleBadge").classList.toggle("hidden", recap.date === todayNPT);

  const barPct = total ? (advances / total) * 100 : 0;
  const barPctDown = total ? (declines / total) * 100 : 0;
  $("breadthBar").innerHTML =
    `<span style="width:${barPct}%;background:var(--green)"></span>` +
    `<span style="width:${barPctDown}%;background:var(--red)"></span>` +
    `<span style="width:${100 - barPct - barPctDown}%;background:var(--text-dimmer)"></span>`;
  $("breadthNums").textContent = `${advances} up · ${declines} down · ${unchanged} flat`;

  $("statTurnover").textContent = `Rs ${fmtCompact(recap.totalTurnover)}`;
  $("statTrans").textContent = (recap.totalTransactions || 0).toLocaleString("en-US");
  const ma = recap.mostActive || {};
  $("statActive").textContent = ma.symbol ? `${ma.symbol} · Rs ${fmtCompact(ma.turnover)}` : "—";
  $("statScrips").textContent = String(total);
}

function moverRow(item) {
  const tr = document.createElement("tr");
  const pctClass = item.diffPct >= 0 ? "up" : "down";
  tr.innerHTML = `
    <td class="sym">${item.symbol}</td>
    <td class="ltp">${fmtNum(item.ltp, 1)}</td>
    <td class="pct ${pctClass}">${fmtPct(item.diffPct)}</td>`;
  tr.querySelector(".sym").addEventListener("click", () => openDetail(item.symbol));
  return tr;
}

function renderMovers() {
  const gBody = $("gainersTable").querySelector("tbody");
  const lBody = $("losersTable").querySelector("tbody");
  gBody.innerHTML = "";
  lBody.innerHTML = "";
  // Derived from the full board (state.latestRows), not the recap feed, which only ever ships 5 of each.
  const byChange = [...state.latestRows].sort((a, b) => (b.diffPct ?? -Infinity) - (a.diffPct ?? -Infinity));
  byChange.slice(0, 8).forEach((g) => gBody.appendChild(moverRow(g)));
  byChange.slice(-8).reverse().forEach((l) => lBody.appendChild(moverRow(l)));
}

function activeRow(item) {
  const tr = document.createElement("tr");
  const pctClass = item.diffPct >= 0 ? "up" : "down";
  tr.innerHTML = `
    <td class="sym">${item.symbol}</td>
    <td class="ltp">Rs ${fmtCompact(item.turnover)}</td>
    <td class="pct ${pctClass}">${fmtPct(item.diffPct)}</td>`;
  tr.querySelector(".sym").addEventListener("click", () => openDetail(item.symbol));
  return tr;
}

function renderMostActive() {
  const body = $("activeTable").querySelector("tbody");
  body.innerHTML = "";
  const byTurnover = [...state.latestRows].sort((a, b) => (b.turnover || 0) - (a.turnover || 0));
  byTurnover.slice(0, 8).forEach((r) => body.appendChild(activeRow(r)));
  $("mostActive").classList.toggle("hidden", byTurnover.length === 0);
}

function matchesSearch(symbol, q) {
  if (symbol.includes(q)) return true;
  const name = state.companyNames.get(symbol);
  return !!name && name.toUpperCase().includes(q);
}

// "Near" a 52-week bound = within 3% of it — same threshold used by most screeners.
const NEAR_52W_PCT = 0.03;

function passesBoardFilter(row) {
  if (state.boardFilter === "near-high") {
    return row.high52 > 0 && row.ltp >= row.high52 * (1 - NEAR_52W_PCT);
  }
  if (state.boardFilter === "near-low") {
    return row.low52 > 0 && row.ltp <= row.low52 * (1 + NEAR_52W_PCT);
  }
  return true;
}

function sortedFilteredRows() {
  const key = state.sortKey;
  const dir = state.sortDir;
  return state.latestRows.filter(passesBoardFilter).sort((a, b) => {
    const av = a[key], bv = b[key];
    if (typeof av === "string") return av.localeCompare(bv) * dir;
    return ((av ?? -Infinity) - (bv ?? -Infinity)) * dir;
  });
}

function renderBoard() {
  const rows = sortedFilteredRows();
  $("boardCount").textContent = `${rows.length} / ${state.latestRows.length}`;
  const body = $("boardBody");
  body.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const r of rows) {
    const tr = document.createElement("tr");
    const pctClass = r.diffPct >= 0 ? "up" : "down";
    const watching = state.watchlist.has(r.symbol);
    tr.innerHTML = `
      <td class="sym-cell"><span class="star-toggle ${watching ? "active" : ""}">${watching ? "★" : "☆"}</span>${r.symbol}</td>
      <td class="num">${fmtNum(r.ltp, 1)}</td>
      <td class="num ${pctClass}">${fmtPct(r.diffPct)}</td>
      <td class="num">${fmtCompact(r.vol)}</td>
      <td class="num">${fmtCompact(r.turnover)}</td>
      <td class="num">${fmtNum(r.high52, 1)}</td>
      <td class="num">${fmtNum(r.low52, 1)}</td>`;
    tr.addEventListener("click", () => openDetail(r.symbol));
    tr.querySelector(".star-toggle").addEventListener("click", (e) => {
      e.stopPropagation();
      toggleWatch(r.symbol);
    });
    frag.appendChild(tr);
  }
  body.appendChild(frag);
}

function renderWatchlist() {
  const section = $("watchlistSection");
  if (!state.watchlist.size) {
    section.classList.add("hidden");
    return;
  }
  section.classList.remove("hidden");
  const body = $("watchlistBody");
  body.innerHTML = "";
  const frag = document.createDocumentFragment();
  for (const symbol of state.watchlist) {
    const row = state.rowsBySymbol.get(symbol);
    if (!row) continue;
    const tr = document.createElement("tr");
    const pctClass = row.diffPct >= 0 ? "up" : "down";
    tr.innerHTML = `
      <td class="sym-cell"><span class="star-toggle active">★</span>${symbol}</td>
      <td>${fmtNum(row.ltp, 1)}</td>
      <td class="${pctClass}">${fmtPct(row.diffPct)}</td>`;
    tr.addEventListener("click", () => openDetail(symbol));
    tr.querySelector(".star-toggle").addEventListener("click", (e) => {
      e.stopPropagation();
      toggleWatch(symbol);
    });
    frag.appendChild(tr);
  }
  body.appendChild(frag);
}

function renderTicker() {
  const top = [...state.latestRows].sort((a, b) => (b.turnover || 0) - (a.turnover || 0)).slice(0, 25);
  if (!top.length) return;
  const itemsHTML = top.map((r) => {
    const cls = r.diffPct >= 0 ? "up" : "down";
    const arrow = r.diffPct >= 0 ? "▲" : "▼";
    return `<span class="ticker-item" data-sym="${r.symbol}"><span class="ticker-sym">${r.symbol}</span> ${fmtNum(r.ltp, 1)} <span class="chg ${cls}">${arrow} ${fmtPct(r.diffPct)}</span></span>`;
  }).join("");
  // duplicated once so the CSS animation (translateX 0 -> -50%) loops seamlessly
  const track = $("tickerTrack");
  track.innerHTML = itemsHTML + itemsHTML;

  // The animation is declared in CSS on an element that starts empty (0 width),
  // so some browsers never (re)start it once real content is injected — force
  // a fresh restart now that the track has its actual size.
  track.style.animation = "none";
  void track.offsetWidth;
  track.style.animation = "";
}

$("ticker").addEventListener("click", (e) => {
  const item = e.target.closest(".ticker-item");
  if (item) openDetail(item.dataset.sym);
});

document.querySelectorAll("#boardTable thead th").forEach((th) => {
  th.addEventListener("click", () => {
    const key = th.dataset.key;
    if (state.sortKey === key) state.sortDir *= -1;
    else { state.sortKey = key; state.sortDir = -1; }
    renderBoard();
  });
});

function setBoardCollapsed(collapsed) {
  $("boardScroll").classList.toggle("collapsed", collapsed);
  $("boardToggle").textContent = collapsed ? "Show all ▾" : "Hide ▴";
}

$("boardToggle").addEventListener("click", () => {
  setBoardCollapsed(!$("boardScroll").classList.contains("collapsed"));
});

$("boardFilters").addEventListener("click", (e) => {
  const btn = e.target.closest(".filter-chip");
  if (!btn) return;
  state.boardFilter = btn.dataset.filter;
  $("boardFilters").querySelectorAll(".filter-chip").forEach((c) => c.classList.toggle("active", c === btn));
  renderBoard();
});

// ---------- quick search (jump to a share) ----------

function renderQuickSearchResults(query) {
  const results = $("quickSearchResults");
  const q = query.trim().toUpperCase();
  if (!q) {
    results.classList.add("hidden");
    results.innerHTML = "";
    return;
  }
  const matches = state.latestRows
    .filter((r) => matchesSearch(r.symbol, q))
    .sort((a, b) => (b.turnover || 0) - (a.turnover || 0))
    .slice(0, 8);

  results.innerHTML = matches.length
    ? matches.map((r) => {
        const cls = r.diffPct >= 0 ? "up" : "down";
        const name = state.companyNames.get(r.symbol);
        const nameHtml = name ? `<span class="qs-name">${name}</span>` : "";
        return `<div class="qs-item" data-sym="${r.symbol}"><span class="qs-sym">${r.symbol}${nameHtml}</span><span class="qs-ltp">${fmtNum(r.ltp, 1)}</span><span class="qs-chg ${cls}">${fmtPct(r.diffPct)}</span></div>`;
      }).join("")
    : `<div class="qs-empty">No match</div>`;
  results.classList.remove("hidden");
}

function closeQuickSearch() {
  $("quickSearch").value = "";
  $("quickSearchResults").classList.add("hidden");
  $("quickSearchResults").innerHTML = "";
}

$("quickSearch").addEventListener("input", (e) => renderQuickSearchResults(e.target.value));

function moveQuickSearchSelection(delta) {
  const items = [...$("quickSearchResults").querySelectorAll(".qs-item")];
  if (!items.length) return;
  const idx = items.findIndex((it) => it.classList.contains("active"));
  items.forEach((it) => it.classList.remove("active"));
  const next = idx < 0 ? (delta > 0 ? 0 : items.length - 1) : (idx + delta + items.length) % items.length;
  items[next].classList.add("active");
  items[next].scrollIntoView({ block: "nearest" });
}

$("quickSearch").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    moveQuickSearchSelection(1);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    moveQuickSearchSelection(-1);
  } else if (e.key === "Enter") {
    const target = $("quickSearchResults").querySelector(".qs-item.active") || $("quickSearchResults").querySelector(".qs-item");
    if (target) {
      openDetail(target.dataset.sym);
      closeQuickSearch();
      e.target.blur();
    }
  } else if (e.key === "Escape") {
    closeQuickSearch();
    e.target.blur();
  }
});

$("quickSearchResults").addEventListener("click", (e) => {
  const item = e.target.closest(".qs-item");
  if (item) {
    openDetail(item.dataset.sym);
    closeQuickSearch();
  }
});

document.addEventListener("click", (e) => {
  if (!$("quickSearchWrap").contains(e.target)) closeQuickSearch();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "/" && document.activeElement.tagName !== "INPUT") {
    e.preventDefault();
    $("quickSearch").focus();
  }
});

// ---------- detail / chart ----------

function resetLoadable(btnId, resultId, idleLabel) {
  const btn = $(btnId);
  btn.textContent = idleLabel;
  btn.disabled = false;
  $(resultId).classList.add("hidden");
  $(resultId).innerHTML = "";
}

async function openDetail(symbol) {
  const row = state.rowsBySymbol.get(symbol);
  if (!row) return;
  location.hash = symbol;
  state.currentSymbol = symbol;

  resetLoadable("trendLoadBtn", "trendResult", "Load trend (~15MB) ▾");
  loadBrokerPositions(symbol);
  loadDividendHistory(symbol);

  $("detailSymbol").textContent = symbol;
  $("detailName").textContent = state.companyNames.get(symbol) || "";
  $("detailLtp").textContent = `Rs ${fmtNum(row.ltp, 1)}`;
  const chg = $("detailChg");
  chg.textContent = fmtPct(row.diffPct);
  chg.className = `detail-chg ${row.diffPct >= 0 ? "up" : "down"}`;
  updateDetailStar();
  renderSectorTag(symbol);
  renderW52Bar(row);

  $("detailGrid").innerHTML = [
    ["Open", fmtNum(row.open, 1)],
    ["High", fmtNum(row.high, 1)],
    ["Low", fmtNum(row.low, 1)],
    ["Prev Close", fmtNum(row.prevClose, 1)],
    ["VWAP", fmtNum(row.vwap, 2)],
    ["Volume", fmtCompact(row.vol)],
    ["Turnover", "Rs " + fmtCompact(row.turnover)],
    ["Transactions", (row.trans ?? "—").toLocaleString?.("en-US") ?? row.trans],
    ["52W High", fmtNum(row.high52, 1)],
    ["52W Low", fmtNum(row.low52, 1)],
  ].map(([label, val]) => `<div class="dstat"><span class="plabel">${label}</span><span class="pval">${val}</span></div>`).join("")
    + `<div class="dstat"><span class="plabel">Vol vs 30D Avg</span><span class="pval" id="volAvgStat">—</span></div>`;

  const detailEl = $("detail");
  const topbar = document.querySelector(".topbar");
  detailEl.style.scrollMarginTop = `${topbar.offsetHeight + 10}px`;
  detailEl.classList.remove("hidden");
  detailEl.scrollIntoView({ behavior: "smooth", block: "start" });

  await loadHistoryAndDraw(symbol);
}

$("detailClose").addEventListener("click", () => {
  $("detail").classList.add("hidden");
  history.replaceState(null, "", location.pathname + location.search);
});

function renderSectorTag(symbol) {
  const el = $("detailSectorTag");
  const sector = state.sectorMap?.[symbol];
  if (!sector) { el.classList.add("hidden"); return; }
  const perf = state.sectorPerf?.find((s) => s.name === sector);
  el.textContent = perf ? `${sector} · ${fmtPct(perf.pct)} today` : sector;
  el.classList.remove("hidden");
}

function renderW52Bar(row) {
  const wrap = $("w52BarWrap");
  if (!(row.high52 > row.low52)) { wrap.classList.add("hidden"); return; }
  const pct = Math.max(0, Math.min(100, ((row.ltp - row.low52) / (row.high52 - row.low52)) * 100));
  $("w52BarFill").style.width = `${pct}%`;
  $("w52BarMarker").style.left = `${pct}%`;
  $("w52BarPctLabel").textContent = `${pct.toFixed(0)}% of range`;
  $("w52BarLowLabel").textContent = `${fmtNum(row.low52, 1)} Low`;
  $("w52BarHighLabel").textContent = `${fmtNum(row.high52, 1)} High`;
  wrap.classList.remove("hidden");
}

// Ports the signal scoring from scripts/stock_info.py's build_sentiment() — same
// logic, condensed for on-page display instead of a Telegram message.
function computeVerdict(row, hist) {
  const cols = hist.cols || [];
  const cIdx = cols.indexOf("c") >= 0 ? cols.indexOf("c") : cols.indexOf("ltp");
  const vIdx = cols.indexOf("vol");
  const rows = hist.rows || [];
  if (cIdx < 0 || rows.length < 15) return null;

  const closes = rows.map((r) => Number(r[cIdx]));
  const lastValid = (arr) => { for (let i = arr.length - 1; i >= 0; i--) if (arr[i] !== null) return arr[i]; return null; };
  const sma20 = lastValid(sma(closes, 20));
  const sma50 = lastValid(sma(closes, 50));
  const sma200 = closes.length >= 200 ? lastValid(sma(closes, 200)) : null;
  const rsiVal = lastValid(rsi(closes, 14));

  let volRatio = null;
  if (vIdx >= 0) {
    const lastVol = Number(rows[rows.length - 1][vIdx]) || 0;
    const lookback = rows.slice(-31, -1);
    if (lookback.length) {
      const avg = lookback.reduce((s, r) => s + (Number(r[vIdx]) || 0), 0) / lookback.length;
      if (avg > 0) volRatio = lastVol / avg;
    }
  }

  const ltp = row.ltp, dayPct = row.diffPct;
  const w52Pct = row.high52 > row.low52 ? ((ltp - row.low52) / (row.high52 - row.low52)) * 100 : null;

  let score = 0;
  const signals = []; // [isPositive, text]

  if (sma20 !== null) {
    const up = ltp > sma20;
    score += up ? 1 : -1;
    signals.push([up, `Price is ${up ? "above" : "below"} its 20-day average — ${up ? "short-term uptrend" : "short-term downtrend"}`]);
  }
  if (sma50 !== null) {
    const up = ltp > sma50;
    score += up ? 1 : -1;
    signals.push([up, `Price is ${up ? "above" : "below"} its 50-day average — ${up ? "medium-term uptrend" : "medium-term downtrend"}`]);
  }
  if (sma200 !== null) {
    const up = ltp > sma200;
    score += up ? 1 : -1;
    signals.push([up, `Price is ${up ? "above" : "below"} its 200-day average — ${up ? "long-term uptrend" : "long-term downtrend"}`]);
    if (sma50 !== null) {
      const golden = sma50 > sma200;
      score += golden ? 1 : -1;
      signals.push([golden, golden ? "50-day above 200-day — golden cross (bullish)" : "50-day below 200-day — death cross (bearish)"]);
    }
  }
  if (rsiVal !== null) {
    if (rsiVal >= 70) { score -= 1; signals.push([false, `RSI ${rsiVal.toFixed(0)} — overbought, may cool off`]); }
    else if (rsiVal <= 30) { score += 1; signals.push([true, `RSI ${rsiVal.toFixed(0)} — oversold, may bounce`]); }
    else if (rsiVal >= 55) { score += 1; signals.push([true, `RSI ${rsiVal.toFixed(0)} — strong momentum`]); }
    else if (rsiVal <= 45) { score -= 1; signals.push([false, `RSI ${rsiVal.toFixed(0)} — weak momentum`]); }
  }
  if (volRatio !== null && volRatio >= 2.0) {
    const pos = dayPct >= 0;
    score += pos ? 1 : -1;
    signals.push([pos, `Volume ${volRatio.toFixed(1)}x normal — ${pos ? "strong buying interest" : "heavy selling pressure"}`]);
  }
  if (w52Pct !== null) {
    if (w52Pct >= 80) signals.push([null, "Near 52-week high — strong run, watch for a pullback"]);
    else if (w52Pct <= 20) signals.push([null, "Near 52-week low — possible opportunity or continued weakness"]);
  }

  let verdict, cls, text;
  if (score >= 2) { verdict = "✅ WATCH"; cls = "watch"; text = "More signals positive than negative."; }
  else if (score >= 0) { verdict = "⚠️ CAUTION"; cls = "caution"; text = "Mixed signals — wait for confirmation."; }
  else { verdict = "❌ AVOID"; cls = "avoid"; text = "More signals negative than positive."; }

  const reasons = signals.filter(([pos]) => pos === null || pos === (score >= 0)).slice(0, 3).map(([, t]) => t);
  return { verdict, cls, text, reasons };
}

function renderVerdict(row, hist) {
  const card = $("verdictCard");
  const result = computeVerdict(row, hist);
  if (!result) { card.classList.add("hidden"); return; }
  card.className = `verdict-card ${result.cls}`;
  $("detailVerdict").textContent = result.verdict;
  $("detailVerdictText").textContent = result.text;
  $("detailVerdictReasons").innerHTML = result.reasons.map((r) => `<li>${r}</li>`).join("");
  card.classList.remove("hidden");
}

function updateDetailStar() {
  const btn = $("detailStar");
  const active = state.watchlist.has(state.currentSymbol);
  btn.textContent = active ? "★ In Watchlist" : "☆ Add to Watchlist";
  btn.classList.toggle("active", active);
}

$("detailStar").addEventListener("click", () => {
  if (state.currentSymbol) toggleWatch(state.currentSymbol);
});

document.querySelectorAll("#rangePicker button").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#rangePicker button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.range = Number(btn.dataset.range);
    drawChart(state.currentHistory);
  });
});

document.querySelectorAll("#indicatorPicker button").forEach((btn) => {
  btn.addEventListener("click", () => {
    const key = btn.dataset.ind;
    state.indicators[key] = !state.indicators[key];
    btn.classList.toggle("active", state.indicators[key]);
    $("rsiWrap").classList.toggle("hidden", !state.indicators.rsi);
    drawChart(state.currentHistory);
  });
});

async function loadHistoryAndDraw(symbol) {
  let hist = state.historyCache.get(symbol);
  if (!hist) {
    try {
      hist = await fetchJSON(`history/${symbol}.json`);
      state.historyCache.set(symbol, hist);
    } catch {
      hist = { cols: [], rows: [] };
    }
  }
  state.currentHistory = hist;
  drawChart(hist);
  updateVolAvgStat(hist);
  const row = state.rowsBySymbol.get(symbol);
  if (row) renderVerdict(row, hist);
}

function updateVolAvgStat(hist) {
  const el = $("volAvgStat");
  if (!el) return;
  const vIdx = hist.cols ? hist.cols.indexOf("vol") : -1;
  const rows = hist.rows || [];
  if (vIdx < 0 || !rows.length) {
    el.textContent = "—";
    return;
  }
  const last = Number(rows[rows.length - 1][vIdx]) || 0;
  const lookback = rows.slice(-31, -1); // up to 30 prior sessions, excluding today
  if (!lookback.length) {
    el.textContent = "—";
    return;
  }
  const avg = lookback.reduce((s, r) => s + (Number(r[vIdx]) || 0), 0) / lookback.length;
  el.textContent = avg ? `${(last / avg).toFixed(1)}x avg (${fmtCompact(avg)})` : "—";
}

function drawChart(hist) {
  const canvas = $("chartCanvas");
  if (!hist || !hist.rows || !hist.rows.length) {
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const cols = hist.cols;
  const idx = (name) => cols.indexOf(name);
  const dIdx = idx("d"), cIdx = idx("c") >= 0 ? idx("c") : idx("ltp"), vIdx = idx("vol");

  // Indicators are computed over the FULL history first (so a zoomed-in range
  // still has correct lookback), then sliced to match the visible window.
  const fullCloses = hist.rows.map((r) => Number(r[cIdx]));
  const fullSMA20 = sma(fullCloses, 20);
  const fullSMA50 = sma(fullCloses, 50);
  const fullRSI = rsi(fullCloses, 14);

  const sliceStart = state.range > 0 ? Math.max(0, hist.rows.length - state.range) : 0;
  let rows = hist.rows.slice(sliceStart);
  const sma20 = fullSMA20.slice(sliceStart);
  const sma50 = fullSMA50.slice(sliceStart);
  const rsiVals = fullRSI.slice(sliceStart);

  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const W = rect.width, H = rect.height;
  const padL = 46, padR = 10, padT = 10, volH = 40, volGap = 8, labelH = 16;
  const padB = volGap + volH + labelH;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;
  const volTop = padT + plotH + volGap;

  ctx.clearRect(0, 0, W, H);

  const closes = rows.map((r) => Number(r[cIdx]));
  const vols = vIdx >= 0 ? rows.map((r) => Number(r[vIdx]) || 0) : [];
  const scaleValues = closes.slice();
  if (state.indicators.sma20) scaleValues.push(...sma20.filter((v) => v !== null));
  if (state.indicators.sma50) scaleValues.push(...sma50.filter((v) => v !== null));
  const min = Math.min(...scaleValues);
  const max = Math.max(...scaleValues);
  const pad = (max - min) * 0.08 || 1;
  const yMin = min - pad, yMax = max + pad;
  const maxVol = Math.max(...vols, 1);

  const xAt = (i) => padL + (plotW * i) / Math.max(rows.length - 1, 1);
  const yAt = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  // grid + labels
  ctx.strokeStyle = "#1a212a";
  ctx.fillStyle = "#46505c";
  ctx.font = "10px ui-monospace, monospace";
  ctx.lineWidth = 1;
  const gridLines = 4;
  for (let g = 0; g <= gridLines; g++) {
    const v = yMin + ((yMax - yMin) * g) / gridLines;
    const y = yAt(v);
    ctx.beginPath();
    ctx.moveTo(padL, y);
    ctx.lineTo(W - padR, y);
    ctx.stroke();
    ctx.fillText(v.toFixed(1), 4, y + 3);
  }

  const trendUp = closes[closes.length - 1] >= closes[0];
  const lineColor = trendUp ? "#3ddc84" : "#ff5c5c";

  // fill under line
  const grad = ctx.createLinearGradient(0, padT, 0, padT + plotH);
  grad.addColorStop(0, trendUp ? "rgba(61,220,132,0.25)" : "rgba(255,92,92,0.25)");
  grad.addColorStop(1, "rgba(0,0,0,0)");
  ctx.beginPath();
  ctx.moveTo(xAt(0), yAt(closes[0]));
  closes.forEach((c, i) => ctx.lineTo(xAt(i), yAt(c)));
  ctx.lineTo(xAt(closes.length - 1), padT + plotH);
  ctx.lineTo(xAt(0), padT + plotH);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();

  // line
  ctx.beginPath();
  closes.forEach((c, i) => {
    const x = xAt(i), y = yAt(c);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = 1.5;
  ctx.stroke();

  // SMA overlays
  if (state.indicators.sma20) drawIndicatorLine(ctx, sma20, xAt, yAt, "#4fd1ff");
  if (state.indicators.sma50) drawIndicatorLine(ctx, sma50, xAt, yAt, "#ffb84d");

  // volume bars
  if (vIdx >= 0) {
    const barW = Math.max(plotW / rows.length - 1, 1);
    vols.forEach((v, i) => {
      const h = (v / maxVol) * volH;
      const x = xAt(i) - barW / 2;
      const y = volTop + (volH - h);
      ctx.fillStyle = i > 0 && closes[i] >= closes[i - 1] ? "rgba(61,220,132,0.5)" : "rgba(255,92,92,0.5)";
      ctx.fillRect(x, y, barW, h);
    });
  }

  // x-axis first/last date labels
  ctx.fillStyle = "#6b7785";
  ctx.fillText(rows[0][dIdx], padL, H - 4);
  const lastLabel = rows[rows.length - 1][dIdx];
  ctx.fillText(lastLabel, W - padR - ctx.measureText(lastLabel).width, H - 4);

  attachCrosshair(canvas, rows, dIdx, cIdx, xAt, yAt, padL, padR, W);

  if (state.indicators.rsi) drawRSI(rows.length, rsiVals);
}

function sma(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

function rsi(values, period = 14) {
  const out = new Array(values.length).fill(null);
  if (values.length < period + 1) return out;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const diff = values[i] - values[i - 1];
    if (diff >= 0) gains += diff; else losses -= diff;
  }
  let avgGain = gains / period, avgLoss = losses / period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  for (let i = period + 1; i < values.length; i++) {
    const diff = values[i] - values[i - 1];
    const gain = diff > 0 ? diff : 0;
    const loss = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

function drawIndicatorLine(ctx, values, xAt, yAt, color) {
  const start = values.findIndex((v) => v !== null);
  if (start < 0) return;
  ctx.beginPath();
  ctx.moveTo(xAt(start), yAt(values[start]));
  for (let i = start + 1; i < values.length; i++) {
    if (values[i] === null) continue;
    ctx.lineTo(xAt(i), yAt(values[i]));
  }
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.25;
  ctx.stroke();
}

function drawRSI(count, rsiVals) {
  const canvas = $("rsiCanvas");
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  const W = rect.width, H = rect.height;
  const padL = 46, padR = 10, padT = 6, padB = 6;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  ctx.clearRect(0, 0, W, H);

  const xAt = (i) => padL + (plotW * i) / Math.max(count - 1, 1);
  const yAt = (v) => padT + plotH - (v / 100) * plotH;

  ctx.strokeStyle = "#1a212a";
  ctx.setLineDash([3, 3]);
  [30, 70].forEach((level) => {
    ctx.beginPath();
    ctx.moveTo(padL, yAt(level));
    ctx.lineTo(W - padR, yAt(level));
    ctx.stroke();
  });
  ctx.setLineDash([]);

  ctx.fillStyle = "#46505c";
  ctx.font = "9px ui-monospace, monospace";
  ctx.fillText("70", 4, yAt(70) + 3);
  ctx.fillText("30", 4, yAt(30) + 3);

  drawIndicatorLine(ctx, rsiVals, xAt, yAt, "#d7dee5");

  const lastValid = [...rsiVals].reverse().find((v) => v !== null);
  if (lastValid !== undefined) {
    ctx.fillStyle = "#6b7785";
    ctx.fillText(`RSI 14: ${lastValid.toFixed(1)}`, W - padR - 60, padT + 8);
  }
}

function attachCrosshair(canvas, rows, dIdx, cIdx, xAt, yAt, padL, padR, W) {
  const tooltip = $("chartTooltip");
  canvas.onmousemove = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    if (mx < padL || mx > W - padR) { tooltip.classList.add("hidden"); return; }
    const ratio = (mx - padL) / (W - padL - padR);
    const i = Math.round(ratio * (rows.length - 1));
    const row = rows[Math.max(0, Math.min(rows.length - 1, i))];
    if (!row) return;
    tooltip.classList.remove("hidden");
    tooltip.style.left = `${mx}px`;
    tooltip.style.top = `${yAt(Number(row[cIdx]))}px`;
    tooltip.innerHTML = `${row[dIdx]} &nbsp; <b>${fmtNum(row[cIdx], 1)}</b>`;
  };
  canvas.onmouseleave = () => tooltip.classList.add("hidden");
}

window.addEventListener("resize", () => {
  if (state.currentHistory && !$("detail").classList.contains("hidden")) {
    drawChart(state.currentHistory);
  }
});

// ---------- broker floorsheet ----------
// Same public source scripts/broker_floorsheet.py uses. It's a ~3-4MB whole-market
// CSV per day, so it's fetched once (on demand, not on page load) and cached.

function nptDateString(offsetDays) {
  const d = new Date(Date.now() - offsetDays * 86400000);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kathmandu" }).format(d);
}

function nptWeekday(offsetDays) {
  const d = new Date(Date.now() - offsetDays * 86400000);
  return new Intl.DateTimeFormat("en-US", { timeZone: "Asia/Kathmandu", weekday: "short" }).format(d);
}

function splitCSVLine(line) {
  const out = [];
  let cur = "", inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else inQuotes = false;
      } else cur += ch;
    } else if (ch === '"') inQuotes = true;
    else if (ch === ",") { out.push(cur); cur = ""; }
    else cur += ch;
  }
  out.push(cur);
  return out;
}

function parseFloorsheetCSV(text) {
  const lines = text.split(/\r?\n/).filter((l) => l.length);
  const headers = splitCSVLine(lines[0]);
  const symIdx = headers.indexOf("stock_symbol");
  const buyerIdx = headers.indexOf("buyer");
  const sellerIdx = headers.indexOf("seller");
  const qtyIdx = headers.indexOf("quantity");
  const rateIdx = headers.indexOf("rate");
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const v = splitCSVLine(lines[i]);
    rows.push({
      symbol: (v[symIdx] || "").trim().toUpperCase(),
      buyer: (v[buyerIdx] || "").trim(),
      seller: (v[sellerIdx] || "").trim(),
      qty: parseFloat(v[qtyIdx]) || 0,
      rate: rateIdx >= 0 ? parseFloat(v[rateIdx]) || 0 : 0,
    });
  }
  return rows;
}

// Walks backward through NPT calendar days (skipping Sat/Sun) collecting up to
// `n` sessions that actually have a published floorsheet (holidays 404). Newest first.
async function fetchFloorsheetDays(n, maxLookback = 15) {
  const results = [];
  for (let i = 0; i < maxLookback && results.length < n; i++) {
    const wd = nptWeekday(i);
    if (wd === "Sat" || wd === "Sun") continue;
    const date = nptDateString(i);
    if (state.floorsheetByDate.has(date)) {
      results.push(state.floorsheetByDate.get(date));
      continue;
    }
    try {
      const r = await fetch(`${FLOORSHEET_BASE}/floorsheet_${date}.csv`, { cache: "no-store" });
      if (!r.ok) continue;
      const text = await r.text();
      const rows = parseFloorsheetCSV(text);
      if (rows.length) {
        const entry = { date, rows };
        state.floorsheetByDate.set(date, entry);
        results.push(entry);
      }
    } catch { /* try next day */ }
  }
  return results;
}

async function fetchLatestFloorsheet() {
  const days = await fetchFloorsheetDays(1);
  return days[0] || null;
}

function netByBrokerForDay(symbol, rows) {
  const net = new Map();
  for (const r of rows) {
    if (r.symbol !== symbol) continue;
    if (r.buyer) net.set(r.buyer, (net.get(r.buyer) || 0) + r.qty);
    if (r.seller) net.set(r.seller, (net.get(r.seller) || 0) - r.qty);
  }
  return net;
}

async function loadBrokerTrend(symbol, sessions = 5) {
  const days = await fetchFloorsheetDays(sessions);
  if (!days.length) return null;
  days.reverse(); // oldest first, for left-to-right display

  const perDay = days.map((d) => netByBrokerForDay(symbol, d.rows));
  const totals = new Map();
  for (const net of perDay) {
    for (const [b, q] of net) totals.set(b, (totals.get(b) || 0) + q);
  }
  const sorted = [...totals.entries()].sort((a, b) => b[1] - a[1]);
  const accumulators = sorted.filter(([, q]) => q > 0).slice(0, 3);
  const distributors = sorted.filter(([, q]) => q < 0).slice(-3).reverse();

  return { dates: days.map((d) => d.date), perDay, brokers: [...accumulators, ...distributors] };
}

function renderTrendResult(symbol, trend) {
  const el = $("trendResult");
  el.classList.remove("hidden");

  if (!trend || !trend.brokers.length) {
    el.innerHTML = `<div class="broker-empty">No broker trend data available for ${symbol} in the last ${trend ? trend.dates.length : 5} sessions.</div>`;
    return;
  }

  const dayLabels = trend.dates.map((d) => d.slice(5));
  el.innerHTML = `
    <div class="broker-meta">${trend.dates[0]} → ${trend.dates[trend.dates.length - 1]} · net shares bought (+) or sold (−) per session</div>
    <table class="broker-table">
      <thead><tr><th>Broker</th>${dayLabels.map((d) => `<th>${d}</th>`).join("")}<th>${dayLabels.length}D Net</th></tr></thead>
      <tbody>
        ${trend.brokers.map(([b, total]) => `
          <tr>
            <td class="broker-id">${b}</td>
            ${trend.perDay.map((day) => {
              const q = Math.round(day.get(b) || 0);
              return `<td class="${q >= 0 ? "up" : "down"}">${q >= 0 ? "+" : ""}${q.toLocaleString("en-US")}</td>`;
            }).join("")}
            <td class="${total >= 0 ? "up" : "down"}">${total >= 0 ? "+" : ""}${Math.round(total).toLocaleString("en-US")}</td>
          </tr>`).join("")}
      </tbody>
    </table>`;
}

$("trendLoadBtn").addEventListener("click", async () => {
  const symbol = state.currentSymbol;
  if (!symbol) return;
  const btn = $("trendLoadBtn");
  btn.disabled = true;
  btn.textContent = "Loading 5-day trend…";
  try {
    const trend = await loadBrokerTrend(symbol, 5);
    renderTrendResult(symbol, trend);
    btn.textContent = "Reload trend ▾";
  } finally {
    btn.disabled = false;
  }
});

// ---------- dividend history ----------

async function fetchDividends(symbol) {
  const r = await fetch(`${DIVIDEND_BASE}/${symbol}/dividend.csv`, { cache: "no-store" });
  if (!r.ok) return null;
  const text = await r.text();
  const lines = text.split(/\r?\n/).filter((l) => l.length);
  if (lines.length < 2) return [];
  const headers = splitCSVLine(lines[0]);
  return lines.slice(1).map((line) => {
    const v = splitCSVLine(line);
    const obj = {};
    headers.forEach((h, i) => (obj[h] = v[i]));
    return obj;
  });
}

async function loadDividendHistory(symbol) {
  const btn = $("dividendLoadBtn");
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const rows = await fetchDividends(symbol);
    const el = $("dividendResult");
    el.classList.remove("hidden");
    if (!rows || !rows.length) {
      el.innerHTML = `<div class="broker-empty">No dividend history found for ${symbol}.</div>`;
    } else {
      el.innerHTML = `
        <table class="broker-table">
          <thead><tr><th>Fiscal Year</th><th>Bonus %</th><th>Cash %</th><th>Total %</th><th>Book Closure</th></tr></thead>
          <tbody>
            ${rows.map((r) => `
              <tr>
                <td class="broker-id">${r.fiscal_year || "—"}</td>
                <td>${r.bonus_share || "—"}</td>
                <td>${r.cash_dividend || "—"}</td>
                <td>${r.total_dividend || "—"}</td>
                <td>${r.book_closure_date || "—"}</td>
              </tr>`).join("")}
          </tbody>
        </table>`;
    }
    btn.textContent = "Reload dividends ▾";
  } finally {
    btn.disabled = false;
  }
}

$("dividendLoadBtn").addEventListener("click", () => {
  if (state.currentSymbol) loadDividendHistory(state.currentSymbol);
});

// ---------- CSV export ----------

function downloadCSV(filename, headers, rows) {
  const escape = (v) => {
    const s = String(v ?? "");
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const lines = [headers.map(escape).join(","), ...rows.map((r) => r.map(escape).join(","))];
  const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

$("boardExportBtn").addEventListener("click", () => {
  const headers = ["symbol", "open", "high", "low", "close", "ltp", "vwap", "vol", "prevClose", "turnover", "trans", "diffPct", "high52", "low52"];
  const rows = sortedFilteredRows().map((r) => headers.map((h) => r[h]));
  downloadCSV(`ghantaghar_board_${nptDateString(0)}.csv`, headers, rows);
});

$("historyExportBtn").addEventListener("click", () => {
  const hist = state.currentHistory;
  if (!hist || !hist.rows || !hist.rows.length || !state.currentSymbol) return;
  downloadCSV(`${state.currentSymbol}_history.csv`, hist.cols, hist.rows);
});

// ---------- modals (about / terms / privacy) ----------

function wireModal(openId, overlayId, closeId) {
  const overlay = $(overlayId);
  $(openId).addEventListener("click", () => overlay.classList.remove("hidden"));
  $(closeId).addEventListener("click", () => overlay.classList.add("hidden"));
  overlay.addEventListener("click", (e) => {
    if (e.target === overlay) overlay.classList.add("hidden");
  });
}

function reloadHome() {
  history.replaceState(null, "", location.pathname + location.search);
  location.reload();
}
$("brand").addEventListener("click", reloadHome);
$("brand").addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    reloadHome();
  }
});

wireModal("aboutOpen", "aboutOverlay", "aboutClose");
wireModal("termsOpen", "termsOverlay", "termsClose");
wireModal("privacyOpen", "privacyOverlay", "privacyClose");

function analyzeBroker(symbol, rows) {
  const trades = rows.filter((r) => r.symbol === symbol);
  if (!trades.length) return { trades: 0 };

  const buy = new Map(), sell = new Map();
  let totalQty = 0;
  for (const t of trades) {
    if (t.buyer) buy.set(t.buyer, (buy.get(t.buyer) || 0) + t.qty);
    if (t.seller) sell.set(t.seller, (sell.get(t.seller) || 0) + t.qty);
    totalQty += t.qty;
  }
  if (!totalQty) return { trades: 0 };

  const brokers = new Set([...buy.keys(), ...sell.keys()]);
  const net = [...brokers].map((b) => [b, (buy.get(b) || 0) - (sell.get(b) || 0)]);
  const accumulators = net.filter(([, q]) => q > 0).sort((a, b) => b[1] - a[1]).slice(0, 3);
  const distributors = net.filter(([, q]) => q < 0).sort((a, b) => a[1] - b[1]).slice(0, 3);

  const activity = [...brokers].map((b) => [b, (buy.get(b) || 0) + (sell.get(b) || 0)]);
  const top3Vol = activity.sort((a, b) => b[1] - a[1]).slice(0, 3).reduce((s, [, v]) => s + v, 0);
  const concentration = (top3Vol / (totalQty * 2)) * 100;

  const totalAccum = accumulators.reduce((s, [, q]) => s + q, 0);
  const totalDistrib = Math.abs(distributors.reduce((s, [, q]) => s + q, 0));
  let verdict, verdictCls;
  if (totalAccum > totalDistrib * 1.5) { verdict = "Bullish lean — institutions are quietly accumulating"; verdictCls = "up"; }
  else if (totalDistrib > totalAccum * 1.5) { verdict = "Bearish lean — institutions are offloading shares"; verdictCls = "down"; }
  else { verdict = "Neutral — buying and selling are roughly balanced"; verdictCls = ""; }

  return { trades: trades.length, totalQty, buy, sell, accumulators, distributors, concentration, verdict, verdictCls };
}

function renderBrokerResult(symbol, date, result) {
  const el = $("brokerResult");
  el.classList.remove("hidden");

  if (!result.trades) {
    el.innerHTML = `<div class="broker-empty">No floorsheet trades found for ${symbol} on ${date}.</div>`;
    return;
  }

  const concCls = result.concentration > 40 ? "high" : result.concentration > 25 ? "mid" : "low";
  const rows = [...result.accumulators, ...result.distributors];

  el.innerHTML = `
    <div class="broker-meta">${date} · ${Math.round(result.totalQty).toLocaleString("en-US")} shares · ${result.trades.toLocaleString("en-US")} contracts</div>
    <div class="broker-badge-row">
      <span class="conc-badge ${concCls}">Top 3 brokers: ${result.concentration.toFixed(1)}% of volume</span>
    </div>
    <div class="broker-verdict ${result.verdictCls}">${result.verdict}</div>
    ${rows.length ? `
    <table class="broker-table">
      <thead><tr><th>Broker</th><th>Buy</th><th>Sell</th><th>Net</th></tr></thead>
      <tbody>
        ${rows.map(([b, q]) => `
          <tr>
            <td class="broker-id">${b}</td>
            <td>${Math.round(result.buy.get(b) || 0).toLocaleString("en-US")}</td>
            <td>${Math.round(result.sell.get(b) || 0).toLocaleString("en-US")}</td>
            <td class="${q >= 0 ? "up" : "down"}">${q >= 0 ? "+" : ""}${Math.round(q).toLocaleString("en-US")}</td>
          </tr>`).join("")}
      </tbody>
    </table>` : ""}
  `;
}

async function loadBrokerPositions(symbol) {
  const btn = $("brokerLoadBtn");
  btn.disabled = true;
  btn.textContent = "Loading floorsheet…";
  try {
    const fs = await fetchLatestFloorsheet();
    if (!fs) {
      $("brokerResult").classList.remove("hidden");
      $("brokerResult").innerHTML = `<div class="broker-empty">Floorsheet unavailable right now — try again later.</div>`;
      btn.textContent = "Load floorsheet ▾";
      btn.disabled = false;
      return;
    }
    const result = analyzeBroker(symbol, fs.rows);
    renderBrokerResult(symbol, fs.date, result);
    btn.textContent = "Reload floorsheet ▾";
  } finally {
    btn.disabled = false;
  }
}

$("brokerLoadBtn").addEventListener("click", () => {
  if (state.currentSymbol) loadBrokerPositions(state.currentSymbol);
});

// ---------- bulk transactions (market-wide largest trades, on demand) ----------

$("bulkLoadBtn").addEventListener("click", async () => {
  const btn = $("bulkLoadBtn");
  btn.disabled = true;
  btn.textContent = "Loading floorsheet…";
  try {
    const fs = await fetchLatestFloorsheet();
    const result = $("bulkResult");
    if (!fs) {
      result.classList.remove("hidden");
      result.innerHTML = `<div class="broker-empty">Floorsheet unavailable right now — try again later.</div>`;
      btn.textContent = "Load bulk trades (~15MB) ▾";
      return;
    }
    const top = [...fs.rows].sort((a, b) => b.qty - a.qty).slice(0, 20);
    result.innerHTML = `
      <div class="broker-meta">${fs.date} · largest trades by quantity</div>
      <table class="board-table">
        <thead><tr><th>Symbol</th><th>Buyer</th><th>Seller</th><th class="num">Quantity</th><th class="num">Price</th></tr></thead>
        <tbody>
          ${top.map((r) => `
            <tr>
              <td class="sym-cell">${r.symbol}</td>
              <td>${r.buyer}</td>
              <td>${r.seller}</td>
              <td class="num">${Math.round(r.qty).toLocaleString("en-US")}</td>
              <td class="num">${fmtNum(r.rate, 2)}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
    result.classList.remove("hidden");
    btn.textContent = "Reload bulk trades ▾";
  } finally {
    btn.disabled = false;
  }
});

$("bulkResult").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  const sym = tr?.querySelector(".sym-cell")?.textContent;
  if (sym && state.rowsBySymbol.has(sym)) openDetail(sym);
});

async function loadDividends() {
  try {
    const data = await fetchJSON("dividends.json");
    if (data.error) throw new Error(data.error);
    const items = data.items || [];
    if (!items.length) return;
    $("dividendsBody").innerHTML = items.map((d) => `
      <tr>
        <td class="sym-cell">${d.symbol}</td>
        <td class="num">${d.bonus || "—"}</td>
        <td class="num">${d.cash || "—"}</td>
        <td class="num">${d.total || "—"}</td>
        <td>${d.closure || "—"}</td>
        <td>${d.fiscalYear || "—"}</td>
      </tr>`).join("");
    await bootDone;
    $("dividendsSection").classList.remove("hidden");
  } catch (err) {
    console.error("dividends fetch failed", err);
  }
}
loadDividends();

$("dividendsBody").addEventListener("click", (e) => {
  const tr = e.target.closest("tr");
  const sym = tr?.querySelector(".sym-cell")?.textContent;
  if (sym && state.rowsBySymbol.has(sym)) openDetail(sym);
});

boot();
