// Modal surfaces (WP-18 · ARC-4/ARCH-5).
//
// Visibility for the server-down, max-results, missing-credentials and settings
// modals, plus the Gmail credential flow (load/clear) that lives inside the
// load-credentials modal. All status output routes through status.js, so this
// module never writes the status log directly.

import { config, csrfFetch as fetch } from "./config.js";
import { endpoints, checkServerAlive } from "./api.js";
import { logReplace } from "./status.js";

const serverDownBackdrop = document.getElementById("server-down-backdrop");
const serverDownRetry = document.getElementById("server-down-retry");
const maxResultsBackdrop = document.getElementById("max-results-backdrop");
const missingTokenBackdrop = document.getElementById("missing-token-backdrop");
const missingTokenClose = document.getElementById("missing-token-close");
const missingTokenContinue = document.getElementById("missing-token-continue");
const settingsBackdrop = document.getElementById("settings-backdrop");
const settingsBtn = document.getElementById("settings-btn");
const settingsClose = document.getElementById("settings-close");
const clearCredsBtn = document.getElementById("clear-creds-btn");
const loadCredsBtn = document.getElementById("load-creds-btn");
const loadCredsFile = document.getElementById("load-creds-file");
const loadCredsBackdrop = document.getElementById("load-creds-backdrop");
const loadCredsClose = document.getElementById("load-creds-close");
const loadCredsContinue = document.getElementById("load-creds-continue");

// --- Modal manager (role=dialog + focus trap + Escape + focus restore) ------
//
// One implementation for every modal (WP-21/JS-7). The dialog markup (role,
// aria-modal, aria-labelledby/aria-label) lives in dashboard.html; this manager
// owns the runtime behaviour: move focus INTO the dialog on open, trap Tab /
// Shift-Tab inside it, close on Escape, and RESTORE focus to the trigger on
// close. A small stack supports the rare nested case (missing-token → settings).
const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

// backdrop -> { panel, close }
const modalRegistry = new Map();
// active modals, innermost last: { backdrop, panel, close, trigger }
const openStack = [];

function panelOf(backdrop) {
  return backdrop.querySelector('[role="dialog"]') || backdrop.firstElementChild || backdrop;
}

function focusablesIn(panel) {
  return Array.from(panel.querySelectorAll(FOCUSABLE_SELECTOR)).filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

function registerModal(backdrop, closeFn) {
  if (!backdrop) return;
  modalRegistry.set(backdrop, { panel: panelOf(backdrop), close: closeFn });
}

function openModal(backdrop) {
  const entry = modalRegistry.get(backdrop);
  if (!entry) {
    // Unregistered fallback: still show it.
    if (backdrop) backdrop.style.display = "flex";
    return;
  }
  if (openStack.some((m) => m.backdrop === backdrop)) return;
  const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  backdrop.style.display = "flex";
  openStack.push({ backdrop, panel: entry.panel, close: entry.close, trigger });
  const focusables = focusablesIn(entry.panel);
  (focusables[0] || entry.panel).focus();
}

function closeModal(backdrop) {
  const idx = openStack.findIndex((m) => m.backdrop === backdrop);
  backdrop.style.display = "none";
  if (idx === -1) return;
  const [entry] = openStack.splice(idx, 1);
  if (entry.trigger && document.contains(entry.trigger)) {
    entry.trigger.focus();
  }
}

document.addEventListener("keydown", (evt) => {
  // Drop any entries whose backdrop was hidden out-of-band (e.g. a test forcing
  // display:none) so the trap never fires for an invisible dialog.
  while (openStack.length) {
    const top = openStack[openStack.length - 1];
    const visible = top.backdrop && top.backdrop.style.display !== "none";
    if (!visible) {
      openStack.pop();
      continue;
    }
    if (evt.key === "Escape") {
      evt.preventDefault();
      top.close();
      return;
    }
    if (evt.key === "Tab") {
      const focusables = focusablesIn(top.panel);
      if (!focusables.length) {
        evt.preventDefault();
        return;
      }
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      const activeEl = document.activeElement;
      if (evt.shiftKey && (activeEl === first || !top.panel.contains(activeEl))) {
        evt.preventDefault();
        last.focus();
      } else if (!evt.shiftKey && (activeEl === last || !top.panel.contains(activeEl))) {
        evt.preventDefault();
        first.focus();
      }
    }
    return;
  }
});

// --- Server-down modal ------------------------------------------------------
let serverDownShown = false;
export function showServerDownModal() {
  if (serverDownShown) return;
  serverDownShown = true;
  openModal(serverDownBackdrop);
}
export function hideServerDownModal() {
  if (!serverDownShown) return;
  serverDownShown = false;
  if (serverDownBackdrop) closeModal(serverDownBackdrop);
}

// --- Max-results modal ------------------------------------------------------
export function showMaxResultsModal() {
  openModal(maxResultsBackdrop);
}
export function hideMaxResultsModal() {
  if (maxResultsBackdrop) closeModal(maxResultsBackdrop);
}

// --- Settings modal ---------------------------------------------------------
export function toggleSettings(open) {
  if (!settingsBackdrop) return;
  if (open) openModal(settingsBackdrop);
  else closeModal(settingsBackdrop);
}

// --- Missing-credentials modal ---------------------------------------------
function showMissingTokenModal() {
  openModal(missingTokenBackdrop);
}
function hideMissingTokenModal() {
  if (missingTokenBackdrop) closeModal(missingTokenBackdrop);
  toggleSettings(true);
}

// Reset the Load-credentials button whenever the settings panel is toggled.
function resetLoadCredsBtn() {
  if (loadCredsBtn) {
    loadCredsBtn.disabled = false;
    loadCredsBtn.textContent = "Load credentials";
  }
}

// --- Gmail credential flow --------------------------------------------------
function wireClearCreds() {
  if (!clearCredsBtn || !endpoints.clearCreds) return;
  clearCredsBtn.addEventListener("click", async () => {
    clearCredsBtn.disabled = true;
    const original = clearCredsBtn.textContent;
    clearCredsBtn.textContent = "Clearing…";
    try {
      const resp = await fetch(endpoints.clearCreds, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      const joinedLogs = Array.isArray(data.logs) ? data.logs.join("\n") : "";
      if (!resp.ok) {
        const msg = data.error || "Failed to clear credentials.";
        const next = joinedLogs ? `${msg}\n${joinedLogs}` : msg;
        logReplace(next);
        alert(msg);
      } else {
        logReplace(joinedLogs || "Credentials reloaded.");
      }
    } catch (err) {
      const msg = String(err || "Failed to load credentials.");
      logReplace(msg);
      alert(msg);
    } finally {
      clearCredsBtn.disabled = false;
      clearCredsBtn.textContent = original || "Clear credentials";
    }
  });
}

function wireLoadCreds() {
  if (!loadCredsBtn || !loadCredsFile || !endpoints.loadCreds) return;

  // WP-13: /load-credentials returns immediately while the Google consent flow
  // runs on a server background thread. Poll the status surface and reflect
  // waiting/done/failed in the existing status area (the full modal UX is
  // WP-23).
  const pollConnectStatus = async () => {
    if (!endpoints.connectStatus) return;
    const deadline = Date.now() + 200000; // a little past the server's ~3 min limit
    while (Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      let data = null;
      try {
        const resp = await fetch(endpoints.connectStatus, { cache: "no-store" });
        if (resp.ok) data = await resp.json();
      } catch (e) {
        // Server briefly unreachable — keep polling until the deadline.
      }
      if (!data) continue;
      if (data.status === "done") {
        logReplace(data.message || "Gmail connected.");
        config.missingToken = false;
        return;
      }
      if (data.status === "failed" || data.status === "idle") {
        logReplace(data.message || "The Gmail connection didn't complete. Try again.");
        return;
      }
      if (data.message) logReplace(data.message);
    }
    logReplace("The Gmail connection didn't complete. Try again.");
  };

  const doLoadCreds = async () => {
    const file = loadCredsFile.files && loadCredsFile.files[0];
    if (!file) return;
    loadCredsBtn.disabled = true;
    const original = loadCredsBtn.textContent;
    loadCredsBtn.textContent = "Loading…";
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const resp = await fetch(endpoints.loadCreds, { method: "POST", body: form });
      const data = await resp.json().catch(() => ({}));
      const joinedLogs = Array.isArray(data.logs) ? data.logs.join("\n") : "";
      if (!resp.ok) {
        const msg = data.error || "Failed to load credentials.";
        const next = joinedLogs ? `${msg}\n${joinedLogs}` : msg;
        logReplace(next);
        alert(msg);
      } else {
        logReplace(joinedLogs || "Credentials saved. Continue in your browser…");
        if (data.status === "waiting") pollConnectStatus();
      }
    } catch (err) {
      const msg = String(err || "Failed to load credentials.");
      logReplace(msg);
      alert(msg);
    } finally {
      loadCredsBtn.disabled = false;
      loadCredsBtn.textContent = original || "Load credentials";
    }
  };

  const openLoadCredsFile = () => {
    if (loadCredsFile) {
      loadCredsFile.value = "";
      loadCredsFile.click();
    }
  };
  const showLoadCredsModal = () => {
    if (loadCredsBackdrop) {
      openModal(loadCredsBackdrop);
    } else {
      openLoadCredsFile();
    }
  };
  const hideLoadCredsModal = () => {
    if (loadCredsBackdrop) closeModal(loadCredsBackdrop);
  };
  const confirmLoadCredsModal = () => {
    hideLoadCredsModal();
    openLoadCredsFile();
  };
  registerModal(loadCredsBackdrop, hideLoadCredsModal);
  if (loadCredsClose) loadCredsClose.addEventListener("click", hideLoadCredsModal);
  if (loadCredsContinue) loadCredsContinue.addEventListener("click", confirmLoadCredsModal);
  if (loadCredsBackdrop) {
    loadCredsBackdrop.addEventListener("click", (e) => {
      if (e.target === loadCredsBackdrop) hideLoadCredsModal();
    });
  }
  loadCredsBtn.addEventListener("click", showLoadCredsModal);
  loadCredsFile.addEventListener("change", () => {
    if (loadCredsFile.files && loadCredsFile.files[0]) {
      doLoadCreds();
    }
  });
}

// Wire every modal's listeners. Called once by main.js after endpoints exist.
export function initModals() {
  // Register every modal with the shared focus-trap/dialog manager.
  registerModal(serverDownBackdrop, hideServerDownModal);
  registerModal(maxResultsBackdrop, hideMaxResultsModal);
  registerModal(settingsBackdrop, () => {
    resetLoadCredsBtn();
    toggleSettings(false);
  });
  registerModal(missingTokenBackdrop, hideMissingTokenModal);

  if (serverDownRetry) {
    serverDownRetry.addEventListener("click", () => checkServerAlive());
  }
  if (maxResultsBackdrop) {
    maxResultsBackdrop.addEventListener("click", (e) => {
      if (e.target === maxResultsBackdrop) hideMaxResultsModal();
    });
  }
  // One consolidated settings-button listener (CQ-33/JS-12 removed the former
  // duplicate): reset the Load button and open the panel in a single handler.
  if (settingsBtn) {
    settingsBtn.addEventListener("click", () => {
      resetLoadCredsBtn();
      toggleSettings(true);
    });
  }
  if (settingsClose) {
    settingsClose.addEventListener("click", () => {
      resetLoadCredsBtn();
      toggleSettings(false);
    });
  }
  if (settingsBackdrop) {
    settingsBackdrop.addEventListener("click", (e) => {
      if (e.target === settingsBackdrop) toggleSettings(false);
    });
  }
  if (missingTokenClose) missingTokenClose.addEventListener("click", hideMissingTokenModal);
  if (missingTokenContinue) missingTokenContinue.addEventListener("click", hideMissingTokenModal);
  if (missingTokenBackdrop) {
    missingTokenBackdrop.addEventListener("click", (e) => {
      if (e.target === missingTokenBackdrop) hideMissingTokenModal();
    });
  }

  wireClearCreds();
  wireLoadCreds();

  if (config.missingToken) showMissingTokenModal();
}
