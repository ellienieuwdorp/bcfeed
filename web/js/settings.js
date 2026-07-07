// Provider configuration (Gmail/IMAP) controller + the cache-reset action
// (WP-18 · ARC-4/ARCH-5). Moved nearly verbatim from the former IIFE; the only
// substantive change is routing its inline status writes through status.js's
// setStatus (the single status-text API) instead of a private copy.

import { csrfFetch as fetch } from "./config.js";
import { endpoints } from "./api.js";
import { setStatus } from "./status.js";
import { state, releases } from "./state.js";
import { renderTable } from "./table.js";
import { toggleSettings } from "./modals.js";
import { showBanner } from "./feedback.js";

const providerSelect = document.getElementById("provider-select");
const gmailConfigPanel = document.getElementById("gmail-config-panel");
const imapConfigPanel = document.getElementById("imap-config-panel");
const imapHost = document.getElementById("imap-host");
const imapPort = document.getElementById("imap-port");
const imapUser = document.getElementById("imap-user");
const imapPass = document.getElementById("imap-pass");
const imapSsl = document.getElementById("imap-ssl");
const imapDiscover = document.getElementById("imap-discover");
const imapFolderSelect = document.getElementById("imap-folder-select");
const imapFolderHelp = document.getElementById("imap-folder-help");
const imapFolderManualToggle = document.getElementById("imap-folder-manual-toggle");
const imapFolderManualWrap = document.getElementById("imap-folder-manual-wrap");
const imapFolderManual = document.getElementById("imap-folder-manual");
const imapDiscoverStatus = document.getElementById("imap-discover-status");
const imapSave = document.getElementById("imap-save");
const imapSaveStatus = document.getElementById("imap-save-status");
const settingsReset = document.getElementById("settings-reset");
const IMAP_DISCOVER_LABEL = "Connect & load folders";
const IMAP_RELOAD_LABEL = "Reload folders";
const imapState = {
  hasSavedPassword: false,
  savedFingerprint: "",
  savedFolder: "",
  loadedFingerprint: "",
  lastLoadedFingerprint: "",
  discoverInFlight: false,
};

function getImapConnectionPayload() {
  return {
    host: imapHost ? imapHost.value.trim() : "",
    port: imapPort ? parseInt(imapPort.value, 10) || 993 : 993,
    username: imapUser ? imapUser.value.trim() : "",
    password: imapPass ? imapPass.value : "",
    use_ssl: imapSsl ? imapSsl.value === "ssl" : true,
  };
}

function getImapConnectionFingerprint(payload) {
  return JSON.stringify([
    payload.host || "",
    payload.port || 993,
    payload.username || "",
    !!payload.use_ssl,
  ]);
}

function currentImapCanReuseSavedPassword(payload = getImapConnectionPayload()) {
  return (
    !!imapState.hasSavedPassword &&
    getImapConnectionFingerprint(payload) === imapState.savedFingerprint
  );
}

function imapConnectionHasChanged(payload = getImapConnectionPayload()) {
  const referenceFingerprint = imapState.savedFingerprint || imapState.lastLoadedFingerprint;
  return !!referenceFingerprint && getImapConnectionFingerprint(payload) !== referenceFingerprint;
}

function isImapManualFolderMode() {
  return !!(imapFolderManualWrap && imapFolderManualWrap.style.display !== "none");
}

function getSelectedImapFolder() {
  const field = isImapManualFolderMode() ? imapFolderManual : imapFolderSelect;
  return field ? field.value.trim() : "";
}

function syncImapUi() {
  const connection = getImapConnectionPayload();
  const hasConnection = !!(
    connection.host &&
    connection.username &&
    (connection.password || currentImapCanReuseSavedPassword(connection))
  );
  const hasFolder = !!getSelectedImapFolder();
  if (imapSave) {
    imapSave.disabled = !(
      hasConnection &&
      hasFolder &&
      imapState.loadedFingerprint === getImapConnectionFingerprint(connection)
    );
  }
  if (imapPass) {
    imapPass.placeholder = imapState.hasSavedPassword ? "••••••••" : "Password";
  }
  if (imapDiscover) {
    imapDiscover.disabled = imapState.discoverInFlight;
    imapDiscover.textContent = imapState.discoverInFlight
      ? "Connecting…"
      : imapState.loadedFingerprint
        ? IMAP_RELOAD_LABEL
        : IMAP_DISCOVER_LABEL;
  }
}

function setImapManualFolderMode(enabled, manualValue = "") {
  if (imapFolderManualWrap) {
    imapFolderManualWrap.style.display = enabled ? "block" : "none";
  }
  if (imapFolderManualToggle) {
    imapFolderManualToggle.textContent = enabled ? "Use folder list" : "Enter folder manually";
  }
  if (enabled && imapFolderManual) {
    imapFolderManual.value = manualValue || imapFolderManual.value || "";
    imapFolderManual.focus();
  }
  syncImapUi();
}

function resetImapFolderState() {
  imapState.loadedFingerprint = "";
  if (imapFolderSelect) {
    imapFolderSelect.innerHTML = "";
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Connect to load folders";
    imapFolderSelect.appendChild(option);
    imapFolderSelect.disabled = true;
  }
  if (imapFolderHelp) {
    imapFolderHelp.textContent =
      "The folder list is loaded after the IMAP connection is confirmed.";
  }
  if (imapFolderManual) {
    imapFolderManual.value = "";
  }
  setStatus(imapDiscoverStatus, "");
  setImapManualFolderMode(false);
}

function renderImapFolders(folders, selectedFolder, recommendedFolder) {
  if (!imapFolderSelect) return;
  imapFolderSelect.innerHTML = "";

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "Choose a folder";
  imapFolderSelect.appendChild(placeholder);

  let matchedSelection = false;
  (folders || []).forEach((folderName) => {
    const option = document.createElement("option");
    option.value = folderName;
    option.textContent =
      folderName === recommendedFolder ? `${folderName} (recommended)` : folderName;
    if (selectedFolder && folderName === selectedFolder) {
      option.selected = true;
      matchedSelection = true;
    }
    imapFolderSelect.appendChild(option);
  });

  if (!matchedSelection && recommendedFolder) {
    imapFolderSelect.value = recommendedFolder;
    matchedSelection = !!imapFolderSelect.value;
  }

  if (!matchedSelection && selectedFolder) {
    setImapManualFolderMode(true, selectedFolder);
  } else {
    setImapManualFolderMode(false);
  }

  imapFolderSelect.disabled = (folders || []).length === 0;
  if (imapFolderHelp) {
    imapFolderHelp.textContent = recommendedFolder
      ? "A recommended folder has been preselected. Review it, then save the configuration."
      : "Choose the folder to scan for Bandcamp release emails.";
  }
}

async function discoverImapFolders({ showStatus = true, autoSelectSaved = false } = {}) {
  if (!endpoints.apiRoot || imapState.discoverInFlight) return;

  const connection = getImapConnectionPayload();
  if (!connection.host || !connection.username) {
    setStatus(
      imapDiscoverStatus,
      "Enter the IMAP server and username before loading folders.",
      "error",
    );
    return;
  }
  if (!connection.password && !currentImapCanReuseSavedPassword(connection)) {
    setStatus(imapDiscoverStatus, "Enter your IMAP password before loading folders.", "error");
    return;
  }

  imapState.discoverInFlight = true;
  syncImapUi();
  if (showStatus) {
    setStatus(imapDiscoverStatus, "Connecting to IMAP and loading folders…");
  }

  try {
    const resp = await fetch(`${endpoints.apiRoot}/imap/discover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ imap_config: connection }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(data.error || `HTTP ${resp.status}`);
    }
    const preferredFolder =
      autoSelectSaved && imapState.savedFolder
        ? imapState.savedFolder
        : data.recommended_folder || "";
    imapState.loadedFingerprint = getImapConnectionFingerprint(connection);
    imapState.lastLoadedFingerprint = imapState.loadedFingerprint;
    renderImapFolders(data.folders || [], preferredFolder, data.recommended_folder || "");
    if (showStatus) {
      setStatus(
        imapDiscoverStatus,
        "Connection verified. Review the folder selection, then save.",
        "success",
      );
    }
  } catch (e) {
    resetImapFolderState();
    setStatus(imapDiscoverStatus, `Error: ${e.message}`, "error");
  } finally {
    imapState.discoverInFlight = false;
    syncImapUi();
  }
}

async function maybeAutoDiscoverImapFolders() {
  if (!providerSelect || providerSelect.value !== "imap") return;
  const connection = getImapConnectionPayload();
  const fingerprint = getImapConnectionFingerprint(connection);
  if (!connection.host || !connection.username) return;
  if (!connection.password && !currentImapCanReuseSavedPassword(connection)) return;
  if (imapState.loadedFingerprint === fingerprint || imapState.discoverInFlight) return;
  await discoverImapFolders({ showStatus: false, autoSelectSaved: true });
}

function updateImapConfigVisibility() {
  if (!providerSelect) return;
  const isImap = providerSelect.value === "imap";
  if (imapConfigPanel) imapConfigPanel.style.display = isImap ? "block" : "none";
  if (gmailConfigPanel) gmailConfigPanel.style.display = isImap ? "none" : "block";
}

async function loadProviderConfig() {
  if (!endpoints.apiRoot) return;
  try {
    const resp = await fetch(`${endpoints.apiRoot}/provider-config`);
    if (!resp.ok) return;
    const providerConfig = await resp.json();
    if (providerSelect) {
      providerSelect.value = providerConfig.provider || "gmail";
    }
    if (providerConfig.imap_config) {
      const imapConfig = providerConfig.imap_config;
      if (imapHost) imapHost.value = imapConfig.host || "";
      if (imapPort) imapPort.value = imapConfig.port || 993;
      if (imapUser) imapUser.value = imapConfig.username || "";
      if (imapSsl) imapSsl.value = imapConfig.use_ssl !== false ? "ssl" : "none";
      imapState.hasSavedPassword = !!imapConfig.has_password;
      imapState.savedFolder = imapConfig.folder || "";
      imapState.savedFingerprint = getImapConnectionFingerprint({
        host: imapConfig.host || "",
        port: imapConfig.port || 993,
        username: imapConfig.username || "",
        use_ssl: imapConfig.use_ssl !== false,
      });
      if (imapPass) {
        imapPass.value = "";
      }
    }
    resetImapFolderState();
    updateImapConfigVisibility();
    await maybeAutoDiscoverImapFolders();
    setStatus(imapSaveStatus, "");
    syncImapUi();
  } catch (e) {
    console.warn("Could not load provider config:", e);
  }
}

async function saveProviderType() {
  if (!endpoints.apiRoot) return;
  try {
    await fetch(`${endpoints.apiRoot}/provider-config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: providerSelect ? providerSelect.value : "gmail" }),
    });
  } catch (e) {
    console.warn("Could not save provider type:", e);
  }
}

async function saveProviderConfig() {
  if (!endpoints.apiRoot) return;
  try {
    const isImap = providerSelect && providerSelect.value === "imap";
    const payload = {
      provider: providerSelect ? providerSelect.value : "gmail",
    };
    if (isImap) {
      const folder = getSelectedImapFolder();
      if (!folder) {
        setStatus(imapSaveStatus, "Choose an IMAP folder before saving.", "error");
        return;
      }
      payload.imap_config = { ...getImapConnectionPayload(), folder };
    }
    if (imapSave) {
      imapSave.disabled = true;
    }
    const resp = await fetch(`${endpoints.apiRoot}/provider-config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok) {
      if (isImap) {
        imapState.hasSavedPassword = imapState.hasSavedPassword || !!payload.imap_config.password;
        imapState.savedFolder = payload.imap_config.folder;
        imapState.savedFingerprint = getImapConnectionFingerprint(payload.imap_config);
        imapState.loadedFingerprint = imapState.savedFingerprint;
        imapState.lastLoadedFingerprint = imapState.savedFingerprint;
        if (imapPass) {
          imapPass.value = "";
        }
        setStatus(imapSaveStatus, "IMAP configuration saved.", "success");
      }
    } else {
      setStatus(imapSaveStatus, `Error: ${data.error || "Failed to save configuration."}`, "error");
    }
  } catch (e) {
    setStatus(imapSaveStatus, "Error: " + e.message, "error");
  } finally {
    syncImapUi();
  }
}

async function performReset() {
  const clearCache = true;
  const clearViewed = true;
  const clearStarred = true;
  let hadError = false;
  if (endpoints.apiRoot) {
    try {
      const resp = await fetch(`${endpoints.apiRoot}/reset-caches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clear_cache: clearCache,
          clear_viewed: clearViewed,
          clear_starred: clearStarred,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      console.warn("Failed to reset via API", err);
      hadError = true;
    }
  } else {
    hadError = true; // cannot clear disk cache without API
  }
  state.viewed = new Set();
  state.starred = new Set();
  releases.forEach((r) => {
    delete r.embed_url;
    delete r.release_id;
    delete r.is_track;
    delete r.description;
    delete r.has_description;
    delete r.art_url;
  });
  renderTable();
  toggleSettings(false);
  if (hadError && clearCache) {
    showBanner(
      "reset-error",
      "Couldn't reach bcfeed. Make sure it's still running, then try again.",
      {
        kind: "error",
      },
    );
  } else {
    window.location.reload();
  }
}

// Wire the provider controller + reset button; called once by main.js.
export function initSettings() {
  if (providerSelect) {
    providerSelect.addEventListener("change", () => {
      updateImapConfigVisibility();
      if (providerSelect.value === "gmail") {
        saveProviderType();
      } else {
        setStatus(
          imapDiscoverStatus,
          "Load folders and save the IMAP configuration to switch providers.",
        );
        setStatus(imapSaveStatus, "");
        maybeAutoDiscoverImapFolders();
      }
    });
  }
  [imapHost, imapPort, imapUser, imapPass, imapSsl].forEach((input) => {
    if (!input) return;
    const eventName = input === imapSsl ? "change" : "input";
    input.addEventListener(eventName, () => {
      resetImapFolderState();
      if (imapConnectionHasChanged()) {
        setStatus(imapDiscoverStatus, "Connection details changed. Reload folders before saving.");
      }
      setStatus(imapSaveStatus, "");
    });
  });
  if (imapDiscover) {
    imapDiscover.addEventListener("click", () => discoverImapFolders({ showStatus: true }));
  }
  if (imapFolderSelect) {
    imapFolderSelect.addEventListener("change", syncImapUi);
  }
  if (imapFolderManualToggle) {
    imapFolderManualToggle.addEventListener("click", () => {
      const nextMode = !isImapManualFolderMode();
      const currentSelection = imapFolderSelect ? imapFolderSelect.value : "";
      setImapManualFolderMode(nextMode, currentSelection);
    });
  }
  if (imapFolderManual) {
    imapFolderManual.addEventListener("input", syncImapUi);
  }
  if (imapSave) imapSave.addEventListener("click", saveProviderConfig);
  if (settingsReset) settingsReset.addEventListener("click", performReset);

  loadProviderConfig();
}
