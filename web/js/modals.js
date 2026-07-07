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

// --- Server-down modal ------------------------------------------------------
let serverDownShown = false;
export function showServerDownModal() {
  if (serverDownShown) return;
  serverDownShown = true;
  if (serverDownBackdrop) serverDownBackdrop.style.display = "flex";
}
export function hideServerDownModal() {
  if (!serverDownShown) return;
  serverDownShown = false;
  if (serverDownBackdrop) serverDownBackdrop.style.display = "none";
}

// --- Max-results modal ------------------------------------------------------
export function showMaxResultsModal() {
  if (maxResultsBackdrop) maxResultsBackdrop.style.display = "flex";
}
export function hideMaxResultsModal() {
  if (maxResultsBackdrop) maxResultsBackdrop.style.display = "none";
}

// --- Settings modal ---------------------------------------------------------
export function toggleSettings(open) {
  if (!settingsBackdrop) return;
  settingsBackdrop.style.display = open ? "flex" : "none";
}

// --- Missing-credentials modal ---------------------------------------------
function showMissingTokenModal() {
  if (missingTokenBackdrop) missingTokenBackdrop.style.display = "flex";
}
function hideMissingTokenModal() {
  if (missingTokenBackdrop) missingTokenBackdrop.style.display = "none";
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
      loadCredsBackdrop.style.display = "flex";
    } else {
      openLoadCredsFile();
    }
  };
  const hideLoadCredsModal = () => {
    if (loadCredsBackdrop) loadCredsBackdrop.style.display = "none";
  };
  const confirmLoadCredsModal = () => {
    hideLoadCredsModal();
    openLoadCredsFile();
  };
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
