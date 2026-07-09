// Network layer (WP-18 · ARC-4/ARCH-5).
//
// Owns the derived endpoint URLs (computed exactly once in initEndpoints) and
// every fetch against the local bcfeed server: release/state loads, the
// viewed/starred persistence writes (including the WP-17 batch), the health
// poll, and the lazy Bandcamp embed enrichment.

import { config, csrfFetch as fetch } from "./config.js";
import { setReleases } from "./state.js";
import { logAppendLine } from "./status.js";

// The single source of truth for endpoint URLs. Populated once, post-config.
export const endpoints = {
  embedProxyUrl: null,
  apiRoot: null,
  apiHost: null,
  health: null,
  clearCreds: null,
  loadCreds: null,
  connectStatus: null,
  starred: null,
  viewed: null,
};

// Endpoint derivation happens exactly ONCE, here, after config is loaded.
export function initEndpoints() {
  const embedProxyUrl = config.embedProxyUrl;
  const apiRoot = embedProxyUrl ? embedProxyUrl.replace(/\/embed-meta.*$/, "") : null;
  let apiHost = null;
  try {
    apiHost = apiRoot ? new URL(apiRoot).hostname : null;
  } catch {
    apiHost = null;
  }
  endpoints.embedProxyUrl = embedProxyUrl;
  endpoints.apiRoot = apiRoot;
  endpoints.apiHost = apiHost;
  endpoints.health = apiRoot ? `${apiRoot}/health` : null;
  endpoints.clearCreds = apiRoot ? `${apiRoot}/clear-credentials` : null;
  endpoints.loadCreds = apiRoot ? `${apiRoot}/load-credentials` : null;
  endpoints.connectStatus = apiRoot ? `${apiRoot}/connect-status` : null;
  endpoints.starred = apiRoot ? `${apiRoot}/starred-state` : null;
  endpoints.viewed = apiRoot ? `${apiRoot}/viewed-state` : null;
}

export async function loadViewedSet() {
  if (!endpoints.apiRoot) throw new Error("bcfeed isn't ready yet.");
  const resp = await fetch(`${endpoints.apiRoot}/viewed-state`);
  if (!resp.ok) throw new Error(`Viewed state unavailable (HTTP ${resp.status})`);
  const data = await resp.json();
  if (data && Array.isArray(data.viewed)) {
    return new Set(data.viewed);
  }
  return new Set();
}

export async function loadStarredSet() {
  if (!endpoints.starred) throw new Error("bcfeed isn't ready yet.");
  const resp = await fetch(endpoints.starred);
  if (!resp.ok) throw new Error(`Starred state unavailable (HTTP ${resp.status})`);
  const data = await resp.json();
  if (data && Array.isArray(data.starred)) {
    return new Set(data.starred);
  }
  return new Set();
}

export async function fetchReleases() {
  if (!endpoints.apiRoot) throw new Error("bcfeed isn't ready yet.");
  const resp = await fetch(`${endpoints.apiRoot}/releases`, { cache: "no-store" });
  if (!resp.ok) throw new Error(`Failed to load releases (HTTP ${resp.status})`);
  const data = await resp.json();
  const list = Array.isArray(data.releases) ? data.releases : [];
  setReleases(list);
}

// --- Persistence writes -----------------------------------------------------
let persistFailureNoted = false;
function notePersistFailure() {
  if (persistFailureNoted) return;
  persistFailureNoted = true;
  logAppendLine("Couldn't save your latest seen/starred change — it may be lost after a reload.");
}

export async function persistViewedRemote(url, isRead) {
  if (!endpoints.apiRoot || !url) return;
  try {
    const resp = await fetch(`${endpoints.apiRoot}/viewed-state`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, read: isRead }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    persistFailureNoted = false;
  } catch (err) {
    console.warn("Failed to persist viewed state to API", err);
    notePersistFailure();
  }
}

// WP-17 · PERF-1/JS-11: bulk mark-seen persists through ONE batch request (the
// server applies it in one store write) instead of one POST per row.
export async function persistViewedBatchRemote(urls, viewed) {
  if (!endpoints.apiRoot || !urls.length) return;
  try {
    const resp = await fetch(`${endpoints.apiRoot}/viewed-state/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ urls, viewed }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    persistFailureNoted = false;
  } catch (err) {
    console.warn("Failed to persist viewed state batch to API", err);
    notePersistFailure();
  }
}

export async function persistStarredRemote(url, starred) {
  if (!endpoints.starred || !url) return;
  try {
    const resp = await fetch(endpoints.starred, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, starred }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    persistFailureNoted = false;
  } catch (err) {
    console.warn("Failed to persist starred state to API", err);
    notePersistFailure();
  }
}

// --- Health poll ------------------------------------------------------------
// The 2-consecutive-failure blip tolerance from WP-07 is unchanged; only the
// presentation moved (WP-22 · UXP-20): instead of a latching full-screen modal,
// main.js registers non-blocking handlers here (banner + control-disabling on
// down, recovery toast on up). The table stays browsable throughout.
let healthFailureCount = 0;
let serverDownActive = false;
const HEALTH_FAILURES_BEFORE_BANNER = 2;

let onServerDown = () => {};
let onServerUp = () => {};
export function setConnectionHandlers(handlers = {}) {
  if (typeof handlers.onDown === "function") onServerDown = handlers.onDown;
  if (typeof handlers.onUp === "function") onServerUp = handlers.onUp;
}

export async function checkServerAlive() {
  if (!endpoints.health) return;
  const online = typeof navigator === "undefined" ? true : navigator.onLine;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const resp = await fetch(endpoints.health, {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    // Healthy again: reset the failure streak and, if we had gone down, recover.
    healthFailureCount = 0;
    if (serverDownActive) {
      serverDownActive = false;
      onServerUp();
    }
  } catch (err) {
    clearTimeout(timer);
    if (!online) {
      // Ignore offline blips; try again on next interval.
      return;
    }
    const apiHost = endpoints.apiHost;
    const isLocalHost = apiHost && ["localhost", "127.0.0.1", location.hostname].includes(apiHost);
    if (!isLocalHost) {
      // Only watch the health of a locally-served app.
      return;
    }
    healthFailureCount += 1;
    if (healthFailureCount >= HEALTH_FAILURES_BEFORE_BANNER && !serverDownActive) {
      serverDownActive = true;
      onServerDown();
    }
  }
}

// --- Bandcamp embed enrichment ---------------------------------------------
function buildEmbedUrl(id, isTrack) {
  if (!id) return null;
  const kind = isTrack ? "track" : "album";
  return `https://bandcamp.com/EmbeddedPlayer/${kind}=${id}/size=large/bgcol=ffffff/linkcol=0687f5/tracklist=true/artwork=small/transparent=true/`;
}

// In-flight /embed-meta requests keyed by release URL, so hover + focus + click
// on the same row share one network fetch instead of racing.
const embedFetchesInFlight = new Map();

// A row is enriched once it carries embed data AND a known description state:
// /releases sends has_description (a boolean — the body itself no longer rides
// the table payload), and a direct /embed-meta fetch sets the description
// string (possibly empty).
export function isEnriched(release) {
  return Boolean(
    release.embed_url &&
    (typeof release.has_description === "boolean" || typeof release.description === "string"),
  );
}

export async function ensureEmbed(release, opts = {}) {
  // The table payload carries no description bodies; the detail row asks for one
  // lazily (withDescription) and /embed-meta serves it from the server's cache.
  // has_description === false means "fetched, none" — no request needed to know
  // there is nothing to show.
  const needsDescription = Boolean(
    opts.withDescription && release.description === undefined && release.has_description !== false,
  );
  if (release.embed_url && !needsDescription) {
    return release.embed_url;
  }
  if (!release.url || !endpoints.embedProxyUrl) return null;

  const pending = embedFetchesInFlight.get(release.url);
  if (pending) return pending;

  const applyEmbedData = (data) => {
    if (!data) return null;
    const embedUrl = data.embed_url || buildEmbedUrl(data.release_id, data.is_track);
    if (embedUrl) release.embed_url = embedUrl;
    if (data.release_id) release.release_id = data.release_id;
    if (typeof data.is_track === "boolean") {
      release.is_track = data.is_track;
    }
    if (typeof data.description === "string") {
      release.description = data.description;
      release.has_description = data.description.length > 0;
    }
    if (data.art_url) release.art_url = data.art_url;
    return embedUrl;
  };

  async function runEmbedFetch() {
    try {
      const response = await fetch(
        `${endpoints.embedProxyUrl}?url=${encodeURIComponent(release.url)}`,
      );
      if (!response.ok) throw new Error(`Couldn't load the player (${response.status}).`);
      const data = await response.json();
      return applyEmbedData(data);
    } catch (err) {
      console.warn("Failed to fetch embed info", err);
      return null;
    } finally {
      embedFetchesInFlight.delete(release.url);
    }
  }

  const fetchPromise = runEmbedFetch();
  embedFetchesInFlight.set(release.url, fetchPromise);
  return fetchPromise;
}
