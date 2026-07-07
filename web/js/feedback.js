// Feedback primitives: toasts + banners (WP-22 · UXP-2/UXP-19/UXP-20).
//
// A deliberately small, dependency-free surface built on the WP-20 tokens. This
// module is a LEAF — it imports nothing from the app graph, only touches the
// DOM — so any module (populate, api, main, modals, settings) may call it
// without risking an import cycle. It owns two persistent/transient primitives:
//
//   showToast(message, {kind})              — transient, auto-dismissing, stacked
//   showBanner(id, message, {kind, action}) — persistent, dismissible, actionable
//   dismissBanner(id)
//
// plus the small server-offline control-disabling helper (UXP-20). Progress and
// the activity/status line live in status.js (the status-surface owner); errors
// and outcomes surface here.

const KIND_ICON = {
  success: "#icon-check-circle",
  error: "#icon-alert-circle",
  danger: "#icon-alert-circle",
  warn: "#icon-alert-triangle",
  info: "#icon-alert-circle",
};

const OFFLINE_MESSAGE = "bcfeed isn't running";
// Mutating controls that must visibly disable (with an explanation) while the
// app is unreachable. The populate + preload buttons have their own state
// owners that also honour state.serverOffline; these two have none, so they are
// toggled directly here.
const OFFLINE_CONTROL_IDS = ["mark-seen", "mark-unseen"];

let bannerRegion = null;
let toastRegion = null;

function ensureRegions() {
  if (!bannerRegion) {
    bannerRegion = document.getElementById("banner-region");
    if (!bannerRegion) {
      bannerRegion = document.createElement("div");
      bannerRegion.id = "banner-region";
      bannerRegion.className = "banner-region";
      const main = document.querySelector("main");
      const table = document.querySelector(".table-wrapper");
      if (main && table) main.insertBefore(bannerRegion, table);
      else if (main) main.appendChild(bannerRegion);
      else document.body.appendChild(bannerRegion);
    }
  }
  if (!toastRegion) {
    toastRegion = document.getElementById("toast-region");
    if (!toastRegion) {
      toastRegion = document.createElement("div");
      toastRegion.id = "toast-region";
      toastRegion.className = "toast-region";
      toastRegion.setAttribute("role", "status");
      toastRegion.setAttribute("aria-live", "polite");
      document.body.appendChild(toastRegion);
    }
  }
}

function iconSvg(kind) {
  const href = KIND_ICON[kind] || null;
  if (!href) return null;
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", href);
  svg.appendChild(use);
  return svg;
}

// --- Toasts -----------------------------------------------------------------
// Transient outcome feedback: auto-dismiss ≈6s, never more than two stacked,
// never used for errors that need an action (those are banners). The region is
// role=status/aria-live=polite so a screen reader hears completions (JS-7).
const TOAST_TTL_MS = 6000;
const TOAST_MAX = 2;

export function showToast(message, opts = {}) {
  if (!message) return null;
  ensureRegions();
  const kind = opts.kind || "info";
  const toast = document.createElement("div");
  toast.className = `toast toast-${kind}`;
  toast.setAttribute("role", "status");
  const icon = iconSvg(kind);
  if (icon) {
    icon.classList.add("toast-icon");
    toast.appendChild(icon);
  }
  const text = document.createElement("span");
  text.className = "toast-message";
  text.textContent = message;
  toast.appendChild(text);
  toastRegion.appendChild(toast);

  // Cap the stack at two: drop the oldest so a burst never towers up the screen.
  while (toastRegion.children.length > TOAST_MAX) {
    toastRegion.removeChild(toastRegion.firstElementChild);
  }

  // Trigger the enter transition on the next frame.
  requestAnimationFrame(() => toast.classList.add("is-visible"));
  const remove = () => {
    toast.classList.remove("is-visible");
    toast.classList.add("is-leaving");
    setTimeout(() => toast.remove(), 200);
  };
  const timer = setTimeout(remove, opts.ttl || TOAST_TTL_MS);
  toast.addEventListener("click", () => {
    clearTimeout(timer);
    remove();
  });
  return toast;
}

// --- Banners ----------------------------------------------------------------
// Persistent, actionable, accurate (UXP-19). Keyed by a stable id so a repeated
// call updates the existing banner in place instead of stacking duplicates; a
// banner is never auto-overwritten by status updates and survives calendar
// clicks / filter changes (it lives in its own region, not the status line).
export function showBanner(id, message, opts = {}) {
  if (!id) return null;
  ensureRegions();
  const kind = opts.kind || "error";
  const isError = kind === "error" || kind === "danger";
  const dismissible = opts.dismissible !== false;

  let banner = bannerRegion.querySelector(`[data-banner-id="${CSS.escape(id)}"]`);
  if (!banner) {
    banner = document.createElement("div");
    banner.dataset.bannerId = id;
    bannerRegion.appendChild(banner);
  } else {
    banner.textContent = "";
  }
  banner.className = `banner banner-${isError ? "danger" : kind}`;
  // Errors announce assertively (role=alert); advisories are polite (role=status).
  banner.setAttribute("role", isError ? "alert" : "status");

  const icon = iconSvg(kind);
  if (icon) {
    icon.classList.add("banner-icon");
    banner.appendChild(icon);
  }
  const text = document.createElement("span");
  text.className = "banner-message";
  text.textContent = message || "";
  banner.appendChild(text);

  if (opts.action && opts.action.label && typeof opts.action.onClick === "function") {
    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "button button-compact banner-action";
    actionBtn.textContent = opts.action.label;
    actionBtn.addEventListener("click", opts.action.onClick);
    banner.appendChild(actionBtn);
  }

  if (dismissible) {
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "button banner-dismiss";
    dismiss.setAttribute("aria-label", "Dismiss");
    const dismissSvg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    dismissSvg.setAttribute("class", "icon");
    dismissSvg.setAttribute("aria-hidden", "true");
    const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#icon-x");
    dismissSvg.appendChild(use);
    dismiss.appendChild(dismissSvg);
    dismiss.addEventListener("click", () => dismissBanner(id));
    banner.appendChild(dismiss);
  }
  return banner;
}

export function dismissBanner(id) {
  if (!bannerRegion || !id) return;
  const banner = bannerRegion.querySelector(`[data-banner-id="${CSS.escape(id)}"]`);
  if (banner) banner.remove();
}

// --- Server-offline control disabling (UXP-20) ------------------------------
// While disconnected, no mutating action may silently fail: disable the ones
// without a state owner (mark seen/unseen) and mark the body so CSS can quiet
// the rest. Populate/preload honour state.serverOffline through their own
// owners; main.js re-runs those owners on reconnect.
export function setControlsOffline(offline) {
  document.body.classList.toggle("is-offline", !!offline);
  OFFLINE_CONTROL_IDS.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    if (offline) {
      if (el.dataset.offlineTitle === undefined) {
        el.dataset.offlineTitle = el.title || "";
      }
      el.disabled = true;
      el.title = OFFLINE_MESSAGE;
    } else {
      el.disabled = false;
      el.title = el.dataset.offlineTitle || "";
      delete el.dataset.offlineTitle;
    }
  });
}
