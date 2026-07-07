// Modal surfaces (WP-18 · ARC-4/ARCH-5; WP-22 · UXP-19/UXP-20).
//
// Visibility for the max-results, missing-credentials and settings modals, plus
// the Gmail credential flow (load/clear) that lives inside the load-credentials
// modal. The server-down full-screen modal is gone — its recovery is now a
// non-blocking banner (see main.js + api.js). Credential outcomes surface as
// toasts (success) / banners (failure); detail still flows to the Details log.

import { config, csrfFetch as fetch } from "./config.js";
import { endpoints } from "./api.js";
import { logAppendLine } from "./status.js";
import { showToast, showBanner, dismissBanner } from "./feedback.js";

const GMAIL_BANNER = "gmail-connect";
const maxResultsBackdrop = document.getElementById("max-results-backdrop");
const settingsBackdrop = document.getElementById("settings-backdrop");
const settingsBtn = document.getElementById("settings-btn");
const settingsClose = document.getElementById("settings-close");
const clearCredsBtn = document.getElementById("clear-creds-btn");
const loadCredsBtn = document.getElementById("load-creds-btn");
const loadCredsFile = document.getElementById("load-creds-file");
const loadCredsBackdrop = document.getElementById("load-creds-backdrop");
const loadCredsModal = loadCredsBackdrop
  ? loadCredsBackdrop.querySelector(".load-creds-modal")
  : null;
const loadCredsClose = document.getElementById("load-creds-close");
const loadCredsContinue = document.getElementById("load-creds-continue");
const loadCredsCancel = document.getElementById("load-creds-cancel");
const loadCredsRetry = document.getElementById("load-creds-retry");
const loadCredsRetryMsg = document.getElementById("load-creds-retry-msg");

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

// --- Generic dialog helpers (WP-23) -----------------------------------------
// Expose the shared focus-trap manager so other modules (settings.js's
// delete-data dialog) register + open + close through the same trap without
// duplicating it.
export function registerDialog(backdrop, closeFn) {
  registerModal(backdrop, closeFn);
}
export function openDialog(backdrop) {
  if (backdrop) openModal(backdrop);
}
export function closeDialog(backdrop) {
  if (backdrop) closeModal(backdrop);
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

// Reset the Connect-Gmail button whenever the settings panel is toggled.
function resetLoadCredsBtn() {
  if (loadCredsBtn) {
    loadCredsBtn.disabled = false;
    loadCredsBtn.textContent = "Connect Gmail…";
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
      if (joinedLogs) logAppendLine(joinedLogs);
      if (!resp.ok) {
        // Plain-language banner — never the raw server/keychain text (WP-22).
        showBanner(GMAIL_BANNER, "Couldn't disconnect Gmail. Restart bcfeed and try again.", {
          kind: "error",
        });
      } else {
        dismissBanner(GMAIL_BANNER);
        config.missingToken = true;
        showToast("Gmail disconnected.", { kind: "success" });
        document.dispatchEvent(new CustomEvent("bcfeed:connection-changed"));
      }
    } catch (err) {
      console.warn("Failed to clear credentials", err);
      showBanner(GMAIL_BANNER, "Couldn't disconnect Gmail. Restart bcfeed and try again.", {
        kind: "error",
      });
    } finally {
      clearCredsBtn.disabled = false;
      clearCredsBtn.textContent = original || "Disconnect Gmail";
    }
  });
}

// The Connect-Gmail modal has three views (intro → waiting → retry). Switching
// views toggles the [data-view] blocks and records the state on the panel.
function setLoadCredsView(view) {
  if (!loadCredsModal) return;
  loadCredsModal.setAttribute("data-state", view);
  loadCredsModal.querySelectorAll(".load-creds-view").forEach((el) => {
    el.hidden = el.getAttribute("data-view") !== view;
  });
  // Keep focus inside the dialog on the newly-shown view's primary control.
  const isOpen = loadCredsBackdrop && loadCredsBackdrop.style.display !== "none";
  const primary = loadCredsModal.querySelector(`.load-creds-view[data-view="${view}"] button`);
  if (isOpen && primary) primary.focus();
}

// Generation token: bumping it makes any in-flight poll loop exit (Cancel / ✕ /
// backdrop / a fresh attempt) so the UI is recoverable in one click (UXP-4).
let connectPollGeneration = 0;

// Open the supervised Connect-Gmail modal, reset to the intro (pre-announce)
// view. Exported so the onboarding checklist's Gmail step reuses it.
export function openGmailConnect() {
  if (!loadCredsBackdrop) return;
  connectPollGeneration += 1; // cancel any stale poll
  setLoadCredsView("intro");
  openModal(loadCredsBackdrop);
}

function hideLoadCredsModal() {
  connectPollGeneration += 1; // stop polling on any close
  if (loadCredsBackdrop) closeModal(loadCredsBackdrop);
  setLoadCredsView("intro"); // reset for next time
}

function openLoadCredsFile() {
  if (loadCredsFile) {
    loadCredsFile.value = "";
    loadCredsFile.click();
  }
}

// WP-13: /load-credentials returns immediately while the Google consent flow
// runs on a server background thread; poll /connect-status and drive the
// waiting → done / retry transitions (UXP-4).
async function pollConnectStatus() {
  if (!endpoints.connectStatus) return;
  const generation = connectPollGeneration;
  const deadline = Date.now() + 200000; // a little past the server's ~3 min limit
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, 2000));
    if (generation !== connectPollGeneration) return; // cancelled / superseded
    let data = null;
    try {
      const resp = await fetch(endpoints.connectStatus, { cache: "no-store" });
      if (resp.ok) data = await resp.json();
    } catch (e) {
      // Server briefly unreachable — keep polling until the deadline.
    }
    if (!data) continue;
    if (data.message) logAppendLine(data.message);
    if (data.status === "done") {
      if (generation !== connectPollGeneration) return;
      config.missingToken = false;
      dismissBanner(GMAIL_BANNER);
      showToast("Gmail connected.", { kind: "success" });
      hideLoadCredsModal();
      document.dispatchEvent(new CustomEvent("bcfeed:connection-changed"));
      return;
    }
    if (data.status === "failed" || data.status === "idle") {
      showConnectRetry("Sign-in wasn't completed. Try again.");
      return;
    }
  }
  showConnectRetry("Sign-in wasn't completed. Try again.");
}

function showConnectRetry(message) {
  if (loadCredsRetryMsg) loadCredsRetryMsg.textContent = message;
  setLoadCredsView("retry");
}

async function doLoadCreds() {
  const file = loadCredsFile && loadCredsFile.files && loadCredsFile.files[0];
  if (!file || !endpoints.loadCreds) return;
  try {
    const form = new FormData();
    form.append("file", file, file.name);
    const resp = await fetch(endpoints.loadCreds, { method: "POST", body: form });
    const data = await resp.json().catch(() => ({}));
    const joinedLogs = Array.isArray(data.logs) ? data.logs.join("\n") : "";
    if (joinedLogs) logAppendLine(joinedLogs);
    if (!resp.ok) {
      showConnectRetry("Couldn't read that access file. Choose the client_secret_….json file.");
      return;
    }
    dismissBanner(GMAIL_BANNER);
    if (data.status === "waiting") {
      connectPollGeneration += 1;
      setLoadCredsView("waiting");
      pollConnectStatus();
    }
  } catch (err) {
    console.warn("Failed to load credentials", err);
    showConnectRetry("Couldn't connect Gmail. Check the access file and try again.");
  }
}

function wireLoadCreds() {
  if (!loadCredsBtn || !loadCredsFile || !endpoints.loadCreds) return;

  registerModal(loadCredsBackdrop, hideLoadCredsModal);
  // ✕ and backdrop CLOSE the modal — they must NEVER open the file picker
  // (WP-07/JS-9 fix preserved). Only "Choose access file…" opens the picker.
  if (loadCredsClose) loadCredsClose.addEventListener("click", hideLoadCredsModal);
  if (loadCredsBackdrop) {
    loadCredsBackdrop.addEventListener("click", (e) => {
      if (e.target === loadCredsBackdrop) hideLoadCredsModal();
    });
  }
  if (loadCredsContinue) loadCredsContinue.addEventListener("click", openLoadCredsFile);
  if (loadCredsCancel) loadCredsCancel.addEventListener("click", hideLoadCredsModal);
  if (loadCredsRetry) loadCredsRetry.addEventListener("click", () => setLoadCredsView("intro"));

  // The Settings "Connect Gmail…" button opens the same supervised modal.
  loadCredsBtn.addEventListener("click", openGmailConnect);
  loadCredsFile.addEventListener("change", () => {
    if (loadCredsFile.files && loadCredsFile.files[0]) {
      doLoadCreds();
    }
  });
}

// Wire every modal's listeners. Called once by main.js after endpoints exist.
export function initModals() {
  // Register every modal with the shared focus-trap/dialog manager.
  registerModal(maxResultsBackdrop, hideMaxResultsModal);
  registerModal(settingsBackdrop, () => {
    resetLoadCredsBtn();
    toggleSettings(false);
  });

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
  wireClearCreds();
  wireLoadCreds();

  // The old "Credentials Needed" modal + its auto-open-Settings behaviour are
  // gone: first-run now shows the onboarding checklist in the main content
  // (onboarding.js), driven off the same has_credentials signal.
}
