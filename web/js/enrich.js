// Ambient enrichment queue + per-row status (WP-24 · UXP-8/UXP-9/UI-13).
//
// "Get releases" is the only fetch concept the user meets. Loading Bandcamp
// players + descriptions is no longer a second button: it is an AMBIENT
// BACKGROUND QUEUE that runs itself after a fetch, politely, and makes its
// progress legible per-row (a quiet ready / loading / unavailable glyph) and in
// aggregate (a chip with a Pause control near the activity strip).
//
// Every fetch this module triggers goes through the server, never a new
// client-side loop or a second unlimited fetch path:
//   - the whole-range queue drives WP-17's `/preload-range-stream` job (2–3
//     workers, all paced by WP-12's shared ~1 rps token bucket) and stops it
//     with `POST /preload-cancel`;
//   - a single release (starred / expanded / hovered) goes through the
//     cache-first `/embed-meta` endpoint (`ensureEmbed`), which shares the same
//     polite fetcher.
//
// Priority: starring an unloaded release fires its `/embed-meta` fetch
// immediately (front of the queue), preserving star-triggers-enrich. The queue
// PAUSES while a Gmail/IMAP fetch (populate) is running — populate.js calls
// pauseForPopulate()/resumeForPopulate() around its run. A release whose page is
// gone (404 / no meta) is marked *unavailable* and never auto-retried this
// session (the server also negative-caches it, LOG-5).

import { endpoints, fetchReleases, ensureEmbed } from "./api.js";
import { csrfFetch } from "./config.js";
import {
  state,
  releases,
  releaseMap,
  releaseKey,
  withinSelectedRange,
  markCachedBadge,
} from "./state.js";
import { showToast } from "./feedback.js";

// Per-release enrichment status the row glyph reads. A release with an
// `embed_url` is always "ready"; a session-unavailable one is "unavailable";
// otherwise this map may hold a transient "loading" while a fetch is in flight.
const enrichStatus = new Map(); // key → "loading" | "ready"
const sessionUnavailable = new Set(); // keys that failed this session — never auto-retried
const loadingKeys = new Set(); // keys the active queue run marked loading

// Chip / queue lifecycle. `queueState` is the observable truth; `populateActive`
// gates the queue off entirely while a Gmail/IMAP fetch runs (politeness).
const lifecycle = {
  queueState: "idle", // "idle" | "running" | "paused"
  pausedByUser: false, // a user Pause must survive a populate resume
  populateActive: false, // a Gmail/IMAP fetch is running → hold the queue
  pendingStart: false, // a run was requested/interrupted; start when clear
  current: 0,
  total: 0,
  lastPrioritized: null, // last release URL jumped to the front (star)
  lastDone: null, // the most recent terminal `done` payload
  es: null, // the active /preload-range-stream EventSource
  savedRange: null, // [start, end] for a paused/deferred run
};

// A tiny read-only surface the Playwright WP-24 spec asserts against.
if (typeof window !== "undefined") {
  window.__bcfeedEnrich = {
    lifecycle,
    statusFor: (key) => {
      const rel = releaseMap.get(key);
      if (rel && rel.embed_url) return "ready";
      if (sessionUnavailable.has(key)) return "unavailable";
      return enrichStatus.get(key) || null;
    },
  };
}

// --- DOM handles -----------------------------------------------------------
const chip = document.getElementById("enrich-chip");
const chipIcon = document.getElementById("enrich-chip-icon");
const chipLabel = document.getElementById("enrich-chip-label");
const pauseBtn = document.getElementById("enrich-pause");
const loadAllBtn = document.getElementById("enrich-load-all");

// --- Per-row glyphs --------------------------------------------------------
const GLYPH = {
  ready: { icon: "#icon-check-circle", cls: "is-ready", title: "Player ready" },
  loading: { icon: "#icon-loader", cls: "is-loading", title: "Loading player…" },
  unavailable: {
    icon: "#icon-alert-circle",
    cls: "is-unavailable",
    title: "Player unavailable — open it on Bandcamp",
  },
};

function statusFor(release) {
  const key = releaseKey(release);
  if (release.embed_url) return "ready";
  if (sessionUnavailable.has(key)) return "unavailable";
  return enrichStatus.get(key) || null;
}

// Paint one row's status glyph. Called from the table render (per row) and from
// the in-place transitions below (found by data-key). Absence of a glyph is the
// norm — a not-yet-fetched release shows nothing (UI-6).
export function applyEnrichGlyph(row, release) {
  if (!row) return;
  const span = row.querySelector("[data-enrich-glyph]");
  if (!span) return;
  const status = statusFor(release);
  if (!status) {
    span.hidden = true;
    span.className = "enrich-glyph";
    span.innerHTML = "";
    span.removeAttribute("title");
    span.removeAttribute("aria-label");
    return;
  }
  const g = GLYPH[status];
  span.hidden = false;
  span.className = `enrich-glyph ${g.cls}`;
  span.title = g.title;
  span.setAttribute("aria-label", g.title);
  span.innerHTML = `<svg class="icon" aria-hidden="true"><use href="${g.icon}"></use></svg>`;
}

function rowForKey(key) {
  if (!key) return null;
  return document.querySelector(`#release-rows tr.data-row[data-key="${CSS.escape(key)}"]`);
}

function updateGlyphForKey(key) {
  const release = releaseMap.get(key);
  if (!release) return;
  const row = rowForKey(key);
  if (row) applyEnrichGlyph(row, release);
}

function markLoading(key) {
  if (!key) return;
  enrichStatus.set(key, "loading");
  updateGlyphForKey(key);
}

function markReady(key) {
  if (!key) return;
  enrichStatus.set(key, "ready");
  const row = rowForKey(key);
  const release = releaseMap.get(key);
  updateGlyphForKey(key);
  if (row && release) markCachedBadge(row, release); // dev "Saved" badge stays behind its toggle
  renderChip();
}

function markUnavailable(key) {
  if (!key) return;
  enrichStatus.delete(key);
  sessionUnavailable.add(key);
  updateGlyphForKey(key);
  renderChip();
}

// True when a release has failed this session — used by the automatic paths
// (hover-prefetch, render-of-starred) so they never re-hit a dead page.
export function isEnrichUnavailable(release) {
  return sessionUnavailable.has(releaseKey(release));
}

// --- Single-release enrichment (star / expand / render-of-starred) ----------
// Goes through the cache-first `/embed-meta` (ensureEmbed): the polite fetcher,
// deduped in-flight. `priority` records the front-of-queue jump for a star.
export function enrichRelease(release, { priority = false } = {}) {
  if (!release || !release.url || !endpoints.embedProxyUrl) return;
  const key = releaseKey(release);
  if (release.embed_url) {
    markReady(key);
    return;
  }
  if (sessionUnavailable.has(key)) {
    updateGlyphForKey(key);
    return;
  }
  if (priority) lifecycle.lastPrioritized = release.url;
  markLoading(key);
  ensureEmbed(release).then((embedUrl) => {
    if (embedUrl) markReady(key);
    else markUnavailable(key);
  });
}

// Star handler hook (table.js): star jumps this release to the front.
export function enrichStar(release) {
  enrichRelease(release, { priority: true });
}

// Expand handler hook (table.js): record the outcome of the detail-row fetch so
// the row glyph agrees with what the detail panel just showed.
export function noteEmbedResult(release, embedUrl) {
  if (!release) return;
  const key = releaseKey(release);
  if (embedUrl) markReady(key);
  else markUnavailable(key);
}

// --- The whole-range background queue --------------------------------------
function currentRange() {
  const from = state.dateFilterFrom || state.dateFilterTo || "";
  const to = state.dateFilterTo || state.dateFilterFrom || "";
  if (!from || !to) return null;
  return from <= to ? [from, to] : [to, from];
}

function pendingInRange() {
  let count = 0;
  for (const rel of releases) {
    if (!rel.url || !withinSelectedRange(rel)) continue;
    const key = releaseKey(rel);
    if (rel.embed_url || sessionUnavailable.has(key)) continue;
    count += 1;
  }
  return count;
}

function markRangeLoading() {
  loadingKeys.clear();
  for (const rel of releases) {
    if (!rel.url || !withinSelectedRange(rel)) continue;
    const key = releaseKey(rel);
    if (rel.embed_url || sessionUnavailable.has(key)) continue;
    loadingKeys.add(key);
    markLoading(key);
  }
}

function clearLoadingGlyphs() {
  loadingKeys.forEach((key) => {
    if (enrichStatus.get(key) === "loading") {
      enrichStatus.delete(key);
      updateGlyphForKey(key);
    }
  });
  loadingKeys.clear();
}

function closeStream() {
  if (lifecycle.es) {
    try {
      lifecycle.es.close();
    } catch (err) {
      /* already closed */
    }
    lifecycle.es = null;
  }
}

// Ask the active server job to stop scheduling new fetches (the in-flight one
// finishes and is persisted — WP-17 cancel semantics).
function cancelServerJob() {
  if (!endpoints.apiRoot) return;
  csrfFetch(`${endpoints.apiRoot}/preload-cancel`, { method: "POST" }).catch((err) => {
    console.warn("Failed to cancel release-details job", err);
  });
}

// Open /preload-range-stream for [start, end]. `manual` (Load all players)
// forces a run even with nothing obviously pending.
function openQueue(startVal, endVal, { manual = false } = {}) {
  if (!endpoints.apiRoot || !window.EventSource) return;
  lifecycle.savedRange = [startVal, endVal];
  const url = `${endpoints.apiRoot}/preload-range-stream?start=${encodeURIComponent(startVal)}&end=${encodeURIComponent(endVal)}`;
  const es = new EventSource(url);
  lifecycle.es = es;
  lifecycle.queueState = "running";
  lifecycle.pausedByUser = false;
  lifecycle.current = 0;
  lifecycle.total = 0;
  markRangeLoading();
  renderChip();

  let finished = false;
  const finish = (cancelled) => {
    if (finished) return;
    finished = true;
    closeStream();
    if (lifecycle.queueState === "running") lifecycle.queueState = "idle";
    onQueueSettled({ cancelled, manual });
  };

  es.onmessage = (ev) => {
    const data = parseData(ev && ev.data);
    if (!data || data.v !== 1) return;
    if (Number.isFinite(Number(data.total))) lifecycle.total = Number(data.total);
    if (data.current != null) lifecycle.current = Number(data.current) || 0;
    renderChip();
  };
  es.addEventListener("error", (ev) => {
    const data = parseData(ev && ev.data);
    if (!data) {
      // Connection-level blip: only a truly closed stream ends the run.
      if (es.readyState === EventSource.CLOSED && !finished) finish(true);
      return;
    }
    // Terminal typed error (e.g. code "busy") — end quietly; the chip resets.
    finish(true);
  });
  es.addEventListener("done", (ev) => {
    const data = parseData(ev && ev.data) || {};
    lifecycle.lastDone = data;
    finish(!!data.cancelled);
  });
}

async function onQueueSettled({ cancelled, manual }) {
  // Refetch so freshly-enriched rows carry embed_url, then let the row glyphs
  // settle: successes → ready, and (only on a completed, non-cancelled run) any
  // release we were loading that still has no player → unavailable.
  try {
    await fetchReleases();
  } catch (err) {
    console.warn("Failed to refresh releases after loading players", err);
  }
  if (!cancelled) {
    loadingKeys.forEach((key) => {
      const rel = releaseMap.get(key);
      if (rel && !rel.embed_url) markUnavailable(key);
      else enrichStatus.delete(key);
    });
    loadingKeys.clear();
  } else {
    clearLoadingGlyphs();
  }
  if (renderTableHook) renderTableHook();
  const done = lifecycle.lastDone || {};
  lifecycle.lastDone = null;
  renderChip();
  if (manual && !cancelled) {
    const ok = Number(done.ok) || 0;
    const failed = Number(done.failed) || 0;
    const parts = [`Players loaded for ${ok} release${ok === 1 ? "" : "s"}`];
    if (failed) parts.push(`${failed} couldn't be loaded`);
    showToast(`${parts.join("; ")}.`, { kind: failed ? "info" : "success" });
  }
}

// main.js injects the table re-render so enrich.js need not import table.js at
// module load; the binding is resolved lazily to keep the module graph acyclic.
let renderTableHook = null;
export function setEnrichRenderHook(fn) {
  renderTableHook = typeof fn === "function" ? fn : null;
}

function startQueue({ manual = false } = {}) {
  if (lifecycle.populateActive) {
    // Politeness: never compete with a Gmail/IMAP fetch. Defer until it ends.
    lifecycle.pendingStart = true;
    if (manual) lifecycle.pausedByUser = false;
    renderChip();
    return;
  }
  if (lifecycle.queueState === "running") return;
  const range = currentRange();
  if (!range) return;
  if (!manual && pendingInRange() === 0) {
    renderChip();
    return;
  }
  openQueue(range[0], range[1], { manual });
}

function pauseQueueByUser() {
  if (lifecycle.queueState !== "running") return;
  cancelServerJob();
  closeStream();
  lifecycle.queueState = "paused";
  lifecycle.pausedByUser = true;
  clearLoadingGlyphs();
  renderChip();
}

function resumeQueueByUser() {
  lifecycle.pausedByUser = false;
  lifecycle.queueState = "idle";
  startQueue({ manual: true });
}

// --- Politeness handshake with the populate run (populate.js) ---------------
export function pauseForPopulate() {
  lifecycle.populateActive = true;
  if (lifecycle.queueState === "running") {
    cancelServerJob();
    closeStream();
    lifecycle.queueState = "idle";
    lifecycle.pendingStart = true; // resume the interrupted run afterwards
    clearLoadingGlyphs();
  }
  renderChip();
}

export function resumeForPopulate() {
  lifecycle.populateActive = false;
  if (lifecycle.pendingStart && !lifecycle.pausedByUser) {
    lifecycle.pendingStart = false;
    startQueue({});
  } else {
    renderChip();
  }
}

// Kick the ambient queue after a fetch adds releases (populate.js).
export function enrichAfterPopulate() {
  if (lifecycle.pausedByUser) {
    renderChip();
    return;
  }
  startQueue({});
}

// --- The aggregate chip ----------------------------------------------------
export function renderChip() {
  if (!chip) return;
  const offline = state.serverOffline;
  const running = lifecycle.queueState === "running";
  const paused = lifecycle.queueState === "paused";
  const pending = pendingInRange();

  if (offline || (!running && !paused && pending === 0)) {
    chip.hidden = true;
    return;
  }
  chip.hidden = false;

  if (chipIcon) chipIcon.classList.toggle("spinning", running);
  if (running) {
    const total = lifecycle.total;
    const current = lifecycle.current;
    if (chipLabel) {
      chipLabel.textContent =
        total > 0 ? `Loading players · ${current} of ${total}` : "Loading players…";
    }
    if (pauseBtn) {
      pauseBtn.hidden = false;
      pauseBtn.textContent = "Pause";
    }
    if (loadAllBtn) loadAllBtn.hidden = true;
    return;
  }
  if (paused) {
    if (chipLabel) {
      chipLabel.textContent = `Paused · ${pending} player${pending === 1 ? "" : "s"} not loaded`;
    }
    if (pauseBtn) {
      pauseBtn.hidden = false;
      pauseBtn.textContent = "Resume";
    }
    if (loadAllBtn) loadAllBtn.hidden = true;
    return;
  }
  // Idle with pending work: the low-key bulk affordance (≤2 clicks, UXP-8).
  if (chipLabel) {
    chipLabel.textContent = `${pending} player${pending === 1 ? "" : "s"} not loaded yet`;
  }
  if (pauseBtn) pauseBtn.hidden = true;
  if (loadAllBtn) loadAllBtn.hidden = false;
}

function parseData(raw) {
  if (typeof raw !== "string" || !raw) return null;
  try {
    const data = JSON.parse(raw);
    return data && typeof data === "object" ? data : null;
  } catch (err) {
    return null;
  }
}

export function initEnrich() {
  if (pauseBtn) {
    pauseBtn.addEventListener("click", () => {
      if (lifecycle.queueState === "running") pauseQueueByUser();
      else if (lifecycle.queueState === "paused") resumeQueueByUser();
    });
  }
  if (loadAllBtn) {
    loadAllBtn.addEventListener("click", () => startQueue({ manual: true }));
  }
  renderChip();
}
