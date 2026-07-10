// Provider configuration (Gmail/IMAP) controller + the cache-reset action
// (WP-18 · ARC-4/ARCH-5). Moved nearly verbatim from the former IIFE; the only
// substantive change is routing its inline status writes through status.js's
// setStatus (the single status-text API) instead of a private copy.

import { config, csrfFetch as fetch } from "./config.js";
import { endpoints, fetchReleases } from "./api.js";
import { setStatus } from "./status.js";
import { state, releases } from "./state.js";
import { renderTable } from "./table.js";
import { fetchScrapeStatus } from "./calendar.js";
import { toggleSettings, registerDialog, openDialog, closeDialog } from "./modals.js";
import { showBanner, showToast } from "./feedback.js";

const settingsBackdrop = document.getElementById("settings-backdrop");
const providerSelect = document.getElementById("provider-select");
const connectionStatusEl = document.getElementById("settings-connection-status");
const connectionPreviewEl = document.getElementById("settings-connection-preview");
const gmailUseBtn = document.getElementById("gmail-use-btn");
const gmailUseStatus = document.getElementById("gmail-use-status");
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
const deleteDataBackdrop = document.getElementById("delete-data-backdrop");
const deleteDataClose = document.getElementById("delete-data-close");
const deleteDataCancel = document.getElementById("delete-data-cancel");
const deleteDataConfirm = document.getElementById("delete-data-confirm");
const deleteDataIncludeStars = document.getElementById("delete-data-include-stars");
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
    imapSave.title = imapSave.disabled
      ? "Load your folders and choose one, then you can save your mail settings."
      : "";
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
      ? "We've picked the folder that looks right. Check it, then save."
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

// Exported so the onboarding controller (which re-hosts the IMAP panel in the
// first-run checklist) can restore the Settings modal's per-provider display.
export function updateImapConfigVisibility() {
  if (!providerSelect) return;
  const isImap = providerSelect.value === "imap";
  if (imapConfigPanel) imapConfigPanel.style.display = isImap ? "block" : "none";
  if (gmailConfigPanel) gmailConfigPanel.style.display = isImap ? "none" : "block";
}

// The last provider config fetched from the server, used to render the
// connection-status line at the top of the Email connection tab (UXP-5). Its
// stored provider is the ACTIVE connection bcfeed fetches with; the dropdown's
// value is only the PREVIEWED one until an explicit Use/Save action (UIP-10).
let lastProviderConfig = null;

function connectionName(provider) {
  return provider === "imap" ? "your mail server" : "Google sign-in";
}

// One plain sentence shown when the dropdown previews a connection that is not
// the active one: names both, says what commits the switch, and warns that
// switching re-checks recent coverage (UIP-10).
function previewSentence(previewed, active) {
  const commit = previewed === "imap" ? "save your mail settings" : "use it for new releases";
  return (
    `You're previewing ${connectionName(previewed)}. bcfeed keeps using ` +
    `${connectionName(active)} until you ${commit} — switching re-checks your recent dates.`
  );
}

// The explicit "Use Google sign-in" button only makes sense once Gmail is
// connected AND it isn't already the active connection.
function syncGmailUi() {
  if (!gmailUseBtn) return;
  const cfg = lastProviderConfig || {};
  const activeProvider = cfg.provider || "gmail";
  const gmailConnected = !!cfg.has_gmail_credentials;
  const alreadyActive = activeProvider === "gmail";
  gmailUseBtn.disabled = !gmailConnected || alreadyActive;
  gmailUseBtn.title = !gmailConnected
    ? "Connect Gmail first, then you can use it for new releases."
    : alreadyActive
      ? "bcfeed already uses Google sign-in for new releases."
      : "";
}

// The connection-status line reflects the ACTIVE connection; a second reserved
// line calls out a previewed-but-uncommitted switch. Gmail uses
// /provider-config's has_gmail_credentials; IMAP uses has_password plus a
// complete host/username/folder.
function updateConnectionStatus() {
  if (!connectionStatusEl) return;
  const cfg = lastProviderConfig || {};
  const activeProvider = cfg.provider || "gmail";
  const previewed = providerSelect ? providerSelect.value : activeProvider;
  const imap = cfg.imap_config || {};
  const gmailConnected = !!cfg.has_gmail_credentials;
  const imapConnected = !!(imap.host && imap.username && imap.has_password && imap.folder);
  const activeConnected = activeProvider === "imap" ? imapConnected : gmailConnected;

  let text;
  if (activeProvider === "imap") {
    text = imapConnected ? "Using your mail server for new releases." : "Not connected yet.";
  } else {
    text = gmailConnected ? "Using Google sign-in for new releases." : "Not connected yet.";
  }
  connectionStatusEl.classList.toggle("is-connected", activeConnected);
  connectionStatusEl.classList.toggle("is-disconnected", !activeConnected);
  const textEl = connectionStatusEl.querySelector(".connection-text");
  if (textEl) textEl.textContent = text;

  if (connectionPreviewEl) {
    const previewing = previewed !== activeProvider;
    connectionPreviewEl.textContent = previewing ? previewSentence(previewed, activeProvider) : "";
    connectionPreviewEl.classList.toggle("is-visible", previewing);
  }
  syncGmailUi();
}

function isSettingsDialogOpen() {
  return !!(settingsBackdrop && settingsBackdrop.style.display === "flex");
}

// Explicitly switch the ACTIVE connection to Gmail. This is the only Settings
// path (besides the IMAP Save button) that mutates the live connection — the
// dropdown itself never does (UIP-10, the auto-save-on-change incident).
async function useGmailForFetching() {
  if (!endpoints.apiRoot) return;
  if (gmailUseBtn) gmailUseBtn.disabled = true;
  try {
    const resp = await fetch(`${endpoints.apiRoot}/provider-config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider: "gmail" }),
    });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    lastProviderConfig = { ...(lastProviderConfig || {}), provider: "gmail" };
    updateConnectionStatus();
    setStatus(gmailUseStatus, "Now using Google sign-in for new releases.", "success");
    document.dispatchEvent(new CustomEvent("bcfeed:connection-changed"));
  } catch (e) {
    setStatus(gmailUseStatus, "Couldn't switch to Google sign-in. Try again.", "error");
  } finally {
    syncGmailUi();
  }
}

async function loadProviderConfig() {
  if (!endpoints.apiRoot) return;
  try {
    const resp = await fetch(`${endpoints.apiRoot}/provider-config`);
    if (!resp.ok) return;
    const providerConfig = await resp.json();
    lastProviderConfig = providerConfig;
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
    updateConnectionStatus();
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
        setStatus(imapSaveStatus, "Mail settings saved.", "success");
        // Reflect the newly-saved connection in the status line + let the
        // onboarding checklist advance without a page reload.
        lastProviderConfig = {
          ...(lastProviderConfig || {}),
          provider: "imap",
          imap_config: {
            ...payload.imap_config,
            has_password: true,
          },
        };
        updateConnectionStatus();
        document.dispatchEvent(new CustomEvent("bcfeed:connection-changed"));
      }
    } else {
      setStatus(imapSaveStatus, `Error: ${data.error || "Failed to save settings."}`, "error");
    }
  } catch (e) {
    setStatus(imapSaveStatus, "Error: " + e.message, "error");
  } finally {
    syncImapUi();
  }
}

// --- Delete-downloaded-data dialog (WP-23 · UXP-7) --------------------------
// A confirmation that enumerates exactly what will be deleted, with a
// stars/seen-history checkbox that defaults OFF, and a post-action toast. No
// destructive action runs on a single click. The flag split is honoured
// server-side (CQ-03), so a default delete keeps stars + seen history.
function updateDeleteConfirmLabel() {
  if (!deleteDataConfirm) return;
  const includeStars = !!(deleteDataIncludeStars && deleteDataIncludeStars.checked);
  deleteDataConfirm.textContent = includeStars ? "Delete everything" : "Delete downloaded data";
}

function openDeleteDataDialog() {
  // Default OFF on every open — the destructive extra is never sticky.
  if (deleteDataIncludeStars) deleteDataIncludeStars.checked = false;
  updateDeleteConfirmLabel();
  openDialog(deleteDataBackdrop);
}

function closeDeleteDataDialog() {
  closeDialog(deleteDataBackdrop);
}

async function performDeleteData() {
  const includeStars = !!(deleteDataIncludeStars && deleteDataIncludeStars.checked);
  let hadError = false;
  if (endpoints.apiRoot) {
    try {
      const resp = await fetch(`${endpoints.apiRoot}/reset-caches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clear_cache: true,
          clear_viewed: includeStars,
          clear_starred: includeStars,
        }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      console.warn("Failed to delete data via API", err);
      hadError = true;
    }
  } else {
    hadError = true; // cannot clear disk data without the local server
  }
  if (hadError) {
    closeDeleteDataDialog();
    showBanner(
      "reset-error",
      "Couldn't reach bcfeed. Make sure it's still running, then try again.",
      { kind: "error" },
    );
    return;
  }
  // Only clear stars/seen locally when the user asked for it.
  if (includeStars) {
    state.viewed = new Set();
    state.starred = new Set();
  }
  releases.forEach((r) => {
    delete r.embed_url;
    delete r.release_id;
    delete r.is_track;
    delete r.description;
    delete r.has_description;
    delete r.art_url;
  });
  closeDeleteDataDialog();
  toggleSettings(false);
  // In-place refresh (no reload, so the toast is actually seen). The data is
  // gone, so the checklist re-evaluates via the connection-changed broadcast.
  try {
    await fetchReleases();
  } catch (err) {
    console.warn("Failed to refresh releases after delete", err);
  }
  renderTable();
  try {
    await fetchScrapeStatus();
  } catch (err) {
    console.warn("Failed to refresh coverage after delete", err);
  }
  showToast(
    includeStars
      ? "Deleted downloaded releases, players, stars, and history."
      : "Deleted downloaded releases and players. Your stars and history were kept.",
    { kind: "success" },
  );
  document.dispatchEvent(new CustomEvent("bcfeed:connection-changed"));
}

// Render the Settings→About version from /config.json's single VERSION source
// (WP-28 · ARCH-9) instead of a hardcoded string. Falls back to a plain label
// when config didn't load, so the section never shows a stale number.
function renderAboutVersion() {
  const el = document.getElementById("about-version");
  if (!el) return;
  const version = config.raw && config.raw.version ? String(config.raw.version) : "";
  el.textContent = version ? `bcfeed v${version}` : "bcfeed";
}

// Tabbed sections inside the Settings dialog (UIP-10). A real
// role=tablist/tab/tabpanel set with roving tabindex + arrow-key/Home/End
// support; moving focus activates the tab (automatic activation). Panel
// geometry is fixed in CSS, so switching tabs never resizes the dialog.
const SETTINGS_TABS = [
  { tab: "settings-tab-appearance", panel: "settings-tabpanel-appearance" },
  { tab: "settings-tab-connection", panel: "settings-tabpanel-connection" },
  { tab: "settings-tab-advanced", panel: "settings-tabpanel-advanced" },
];

function initSettingsTabs() {
  const tabEls = SETTINGS_TABS.map((t) => document.getElementById(t.tab));
  const panelEls = SETTINGS_TABS.map((t) => document.getElementById(t.panel));
  if (tabEls.some((el) => !el) || panelEls.some((el) => !el)) return;

  function activate(index, { focus = true } = {}) {
    tabEls.forEach((tabEl, i) => {
      const selected = i === index;
      tabEl.setAttribute("aria-selected", selected ? "true" : "false");
      tabEl.tabIndex = selected ? 0 : -1;
      panelEls[i].hidden = !selected;
    });
    if (focus) tabEls[index].focus();
  }

  tabEls.forEach((tabEl, i) => {
    tabEl.addEventListener("click", () => activate(i, { focus: false }));
    tabEl.addEventListener("keydown", (e) => {
      let next = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % tabEls.length;
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
        next = (i - 1 + tabEls.length) % tabEls.length;
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = tabEls.length - 1;
      if (next === null) return;
      e.preventDefault();
      activate(next);
    });
  });
}

// Wire the provider controller + reset button; called once by main.js.
export function initSettings() {
  renderAboutVersion();
  initSettingsTabs();
  if (providerSelect) {
    providerSelect.addEventListener("change", () => {
      updateImapConfigVisibility();
      updateConnectionStatus();
      setStatus(imapSaveStatus, "");
      if (providerSelect.value === "imap") {
        setStatus(
          imapDiscoverStatus,
          "Load folders and save your mail settings to finish switching.",
        );
        maybeAutoDiscoverImapFolders();
      } else if (!isSettingsDialogOpen()) {
        // Onboarding drives this same select while the Settings dialog is
        // CLOSED and relies on the Gmail choice persisting (onboarding.js
        // re-hosts the IMAP panel + reuses this select). Inside the OPEN
        // Settings dialog a Gmail selection is a PREVIEW only — the active
        // connection changes solely via the explicit "Use Google sign-in"
        // button (UIP-10, no auto-save-on-change).
        saveProviderType();
      }
    });
  }
  if (gmailUseBtn) gmailUseBtn.addEventListener("click", useGmailForFetching);
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

  // Delete-downloaded-data confirmation dialog (UXP-7). Registered with the
  // shared focus-trap manager so Escape/backdrop close it and restore focus.
  if (deleteDataBackdrop) {
    registerDialog(deleteDataBackdrop, closeDeleteDataDialog);
    deleteDataBackdrop.addEventListener("click", (e) => {
      if (e.target === deleteDataBackdrop) closeDeleteDataDialog();
    });
  }
  if (settingsReset) settingsReset.addEventListener("click", openDeleteDataDialog);
  if (deleteDataClose) deleteDataClose.addEventListener("click", closeDeleteDataDialog);
  if (deleteDataCancel) deleteDataCancel.addEventListener("click", closeDeleteDataDialog);
  if (deleteDataIncludeStars) {
    deleteDataIncludeStars.addEventListener("change", updateDeleteConfirmLabel);
  }
  if (deleteDataConfirm) deleteDataConfirm.addEventListener("click", () => performDeleteData());

  loadProviderConfig();
}
