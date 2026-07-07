// First-run onboarding checklist (WP-23 · UXP-3/4/6).
//
// When bcfeed has no downloaded releases yet, the main content shows a
// sequenced, provider-branching setup checklist instead of the empty table —
// replacing the deleted "Credentials Needed" modal and its auto-open-Settings
// behaviour. The step is DETECTED, not stored: a half-finished setup resumes at
// the right step on relaunch (client config / token / IMAP config / data
// present). The shipped IMAP connect→discover→folder panel is RE-HOSTED here
// (physically moved into step 2), never rebuilt; the Gmail path opens the
// supervised connect modal (modals.js). Step 3 offers the one-click
// "Check the last 30 days" starter.

import { config, csrfFetch as fetch } from "./config.js";
import { endpoints } from "./api.js";
import { releases, getLastSelectableDate, isoKeyFromDate } from "./state.js";
import { isPopulating } from "./populate.js";
import { openGmailConnect } from "./modals.js";
import { updateImapConfigVisibility } from "./settings.js";

const section = document.getElementById("onboarding");
const step1 = document.getElementById("onboarding-step-1");
const step2 = document.getElementById("onboarding-step-2");
const step3 = document.getElementById("onboarding-step-3");
const providerCards = document.querySelectorAll("#onboarding .provider-card");
const changeProviderBtn = document.getElementById("onboarding-change-provider");
const providerNameEl = document.getElementById("onboarding-provider-name");
const gmailConnect = document.getElementById("onboarding-gmail-connect");
const imapConnect = document.getElementById("onboarding-imap-connect");
const imapSlot = document.getElementById("onboarding-imap-slot");
const gmailBtn = document.getElementById("onboarding-gmail-btn");
const check30Btn = document.getElementById("onboarding-check-30");

const providerSelect = document.getElementById("provider-select");
const imapPanel = document.getElementById("imap-config-panel");
const populateBtn = document.getElementById("populate-range");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");

// The IMAP panel's original home in the Settings modal, so it can be restored
// once setup completes (re-host, don't rebuild).
const imapHome = imapPanel ? { parent: imapPanel.parentNode, next: imapPanel.nextSibling } : null;
let panelMounted = false;

// Detected connection state, refreshed from /config.json + /provider-config.
const detected = {
  connected: false,
  provider: "gmail",
  gmailConfigured: false,
  imapStarted: false,
};
// The provider the user explicitly picked this session (drives step 2 before any
// server-side progress exists); forceChoose sends them back to the card choice.
let chosenProvider = null;
let forceChoose = false;

function detectedPath() {
  if (detected.gmailConfigured) return "gmail";
  if (detected.imapStarted) return "imap";
  return null;
}

// Which step the checklist should show, or hidden once step 3 is complete (any
// release data exists). Never re-shows a completed setup.
function computeStep() {
  if (releases.length > 0) return { visible: false };
  if (detected.connected) return { visible: true, step: 3, provider: detected.provider };
  if (forceChoose) return { visible: true, step: 1, provider: null };
  const path = chosenProvider || detectedPath();
  if (path) return { visible: true, step: 2, provider: path };
  return { visible: true, step: 1, provider: null };
}

function mountImapPanel() {
  if (panelMounted || !imapPanel || !imapSlot) return;
  imapSlot.appendChild(imapPanel);
  panelMounted = true;
}

function unmountImapPanel() {
  if (!panelMounted || !imapPanel || !imapHome) return;
  imapHome.parent.insertBefore(imapPanel, imapHome.next);
  panelMounted = false;
  // Restore the Settings modal's per-provider panel display.
  try {
    updateImapConfigVisibility();
  } catch (e) {
    // settings controller may be unavailable in a degraded load
  }
}

function setStepState(el, current, n) {
  if (!el) return;
  el.classList.toggle("is-done", n < current);
  el.classList.toggle("is-current", n === current);
  el.classList.toggle("is-upcoming", n > current);
}

function applyProviderPath(provider) {
  // Point the (hidden-in-checklist) provider select at the chosen path so the
  // re-hosted IMAP panel's own display logic shows the right fields, then reveal
  // the matching connect panel.
  if (providerSelect && provider) providerSelect.value = provider;
  try {
    updateImapConfigVisibility();
  } catch (e) {
    // no-op if the settings controller isn't ready
  }
  const isImap = provider === "imap";
  if (imapConnect) imapConnect.hidden = !isImap;
  if (gmailConnect) gmailConnect.hidden = isImap;
  if (providerNameEl) {
    providerNameEl.textContent = isImap
      ? "Setting up an IMAP mailbox."
      : "Setting up Google sign-in.";
  }
}

function showChecklist(s) {
  mountImapPanel();
  if (section) section.hidden = false;
  document.body.classList.add("has-onboarding");
  setStepState(step1, s.step, 1);
  setStepState(step2, s.step, 2);
  setStepState(step3, s.step, 3);
  if (s.step === 2) applyProviderPath(s.provider);
}

function hideChecklist() {
  if (section) section.hidden = true;
  document.body.classList.remove("has-onboarding");
}

function render() {
  if (!section) return;
  const s = computeStep();
  if (!s.visible) {
    hideChecklist();
    unmountImapPanel();
    return;
  }
  // Never flip the checklist on mid-run: the activity strip owns the screen
  // while a fetch streams; the post-run re-render re-evaluates.
  if (isPopulating) {
    hideChecklist();
    return;
  }
  showChecklist(s);
}

function chooseProvider(provider) {
  if (!provider) return;
  chosenProvider = provider;
  forceChoose = false;
  if (providerSelect && providerSelect.value !== provider) {
    providerSelect.value = provider;
    // Let the settings controller persist the choice (Gmail) / prime IMAP
    // discovery — the shipped behaviour, reused rather than duplicated.
    providerSelect.dispatchEvent(new Event("change"));
  }
  render();
}

function checkLast30() {
  if (!dateFilterFrom || !dateFilterTo) return;
  const end = getLastSelectableDate();
  const start = new Date(end);
  start.setDate(end.getDate() - 29);
  dateFilterFrom.value = isoKeyFromDate(start);
  dateFilterTo.value = isoKeyFromDate(end);
  // Drive calendar.js so the selection is VISIBLE on the calendar before the run.
  dateFilterFrom.dispatchEvent(new Event("input", { bubbles: true }));
  dateFilterTo.dispatchEvent(new Event("input", { bubbles: true }));
  // Start the fetch through the existing primary action.
  if (populateBtn) populateBtn.click();
}

async function fetchState() {
  const configUrl = endpoints.apiRoot ? `${endpoints.apiRoot}/config.json` : "config.json";
  const providerUrl = endpoints.apiRoot ? `${endpoints.apiRoot}/provider-config` : null;
  try {
    const [cfgResp, provResp] = await Promise.all([
      fetch(configUrl, { cache: "no-store" }),
      providerUrl ? fetch(providerUrl, { cache: "no-store" }) : Promise.resolve(null),
    ]);
    const cfg = cfgResp && cfgResp.ok ? await cfgResp.json() : {};
    const prov = provResp && provResp.ok ? await provResp.json() : {};
    const imap = (prov && prov.imap_config) || {};
    detected.connected = !!cfg.has_credentials;
    detected.provider = (prov && prov.provider) || "gmail";
    detected.gmailConfigured = !!(prov && prov.has_gmail_credentials);
    detected.imapStarted = !!(imap.host || imap.username || imap.has_password);
  } catch (e) {
    // Fall back to the config loaded at startup.
    detected.connected = !config.missingToken;
  }
}

// Wire listeners once; called by main.js during bootstrap. The initial
// show/hide decision happens in refreshOnboarding() after release data loads.
export function initOnboarding() {
  if (!section) return;
  providerCards.forEach((card) => {
    card.addEventListener("click", () => chooseProvider(card.dataset.provider));
  });
  if (changeProviderBtn) {
    changeProviderBtn.addEventListener("click", () => {
      forceChoose = true;
      chosenProvider = null;
      render();
    });
  }
  if (gmailBtn) gmailBtn.addEventListener("click", () => openGmailConnect());
  if (check30Btn) check30Btn.addEventListener("click", checkLast30);
  // Any fetch start quiets the checklist so the run is visible.
  if (populateBtn) {
    populateBtn.addEventListener("click", () => {
      if (section && !section.hidden) hideChecklist();
    });
  }
  // When release data appears (a fetch finished), re-evaluate → the checklist
  // hides itself and hands over to the table.
  const rows = document.getElementById("release-rows");
  if (rows) {
    new MutationObserver(() => render()).observe(rows, { childList: true });
  }
  // Gmail connect / IMAP save / disconnect / delete-data broadcast this so the
  // checklist advances (or returns) without a page reload.
  document.addEventListener("bcfeed:connection-changed", () => {
    refreshOnboarding();
  });
}

// Refresh detected state from the server, then show/hide the checklist at the
// right step. Called after the initial data load and on connection changes.
export async function refreshOnboarding() {
  if (!section) return;
  await fetchState();
  render();
}
