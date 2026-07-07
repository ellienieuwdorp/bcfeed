// Runtime configuration (WP-18 · ARC-4/ARCH-5).
//
// Loads /config.json exactly once and exposes the derived runtime flags plus
// the anti-CSRF fetch wrapper. Endpoint URLs are derived once, in api.js
// (initEndpoints), from config.embedProxyUrl — never twice (kills the old
// double derivation, CQ-33/JS-12).

export const config = {
  raw: {},
  embedProxyUrl: "http://localhost:5050/embed-meta",
  clearStatusOnLoad: false,
  showDevSettings: false,
  missingToken: false,
  defaultTheme: "light",
};

// Anti-CSRF wrapper (WP-08/SEC-11): the server rejects any state-mutating
// request that lacks the X-BCFeed-Request header. Cross-site pages cannot set
// custom headers, so this blocks drive-by CSRF. Every module issues requests
// through this wrapper, so non-GET methods always carry the header.
// (EventSource stays header-free by design; the server's Host check protects
// the SSE endpoints.)
export function csrfFetch(input, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  if (method === "GET" || method === "HEAD") return window.fetch(input, options);
  const headers = new Headers(options.headers || {});
  headers.set("X-BCFeed-Request", "1");
  return window.fetch(input, { ...options, headers });
}

export function asBool(val) {
  if (typeof val === "boolean") return val;
  if (val == null) return false;
  if (typeof val === "number") return !!val;
  if (typeof val === "string") return ["1", "true", "yes", "on"].includes(val.trim().toLowerCase());
  return false;
}

// Fetch and normalize the runtime config. Called once by main.js before any
// endpoint is derived or any bootstrap step runs.
export async function initConfig() {
  const raw = await window
    .fetch("config.json", { cache: "no-store" })
    .then((resp) => (resp.ok ? resp.json() : {}))
    .catch(() => ({}));
  config.raw = raw && typeof raw === "object" ? raw : {};

  const proxyCandidate = config.raw.embed_proxy_url
    ? String(config.raw.embed_proxy_url)
    : config.embedProxyUrl;
  const normalizedProxy = proxyCandidate ? proxyCandidate.replace(/\/+$/, "") : "";
  config.embedProxyUrl = normalizedProxy || null;

  if (config.raw.default_theme) config.defaultTheme = config.raw.default_theme;
  if (typeof config.raw.clear_status_on_load !== "undefined") {
    config.clearStatusOnLoad = asBool(config.raw.clear_status_on_load);
  }
  if (typeof config.raw.show_dev_settings !== "undefined") {
    config.showDevSettings = asBool(config.raw.show_dev_settings);
  }
  if (typeof config.raw.has_credentials !== "undefined") {
    config.missingToken = !asBool(config.raw.has_credentials);
  } else if (typeof config.raw.has_token !== "undefined") {
    // Fallback for backward compatibility.
    config.missingToken = !asBool(config.raw.has_token);
  }
}
