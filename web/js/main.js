// Bootstrap (WP-18 · ARC-4/ARCH-5).
//
// Loaded as <script type="module">, so the DOM is already parsed. Orchestrates
// the ordered startup: load config → derive endpoints once → wire the render
// scheduler → attach every module's listeners → fetch data and paint.

import { initConfig, config } from "./config.js";
import {
  initEndpoints,
  fetchReleases,
  loadViewedSet,
  loadStarredSet,
  checkServerAlive,
  setConnectionHandlers,
} from "./api.js";
import { state, setRenderHandlers, renderCounts } from "./state.js";
import {
  setLoading,
  hideLoading,
  showError,
  showNotice,
  updateHeaderRange,
  toggleDetails,
} from "./status.js";
import { renderTable, refreshToggleButtons, initTable } from "./table.js";
import {
  renderCalendar,
  initCalendar,
  setDefaultDateFilters,
  fetchScrapeStatus,
} from "./calendar.js";
import { initModals } from "./modals.js";
import { initPopulate, updatePopulateButton } from "./populate.js";
import { initSettings } from "./settings.js";
import { showToast, showBanner, dismissBanner, setControlsOffline } from "./feedback.js";

const THEME_KEY = "bc_dashboard_theme";
const SHOW_CACHED_KEY = "bc_show_cached_badges";
const themeToggleBtn = document.getElementById("theme-toggle");

function applyDevSettingsVisibility() {
  document.querySelectorAll(".dev-setting").forEach((el) => {
    el.style.display = config.showDevSettings ? "" : "none";
  });
}

function initTheme() {
  // JS-13/UX-18/ARCH-11: seed from prefers-color-scheme when the user has made
  // no explicit choice, persist ONLY an explicit choice, and never force-default
  // to dark. A stored "light"/"dark" always wins over the OS preference.
  const applyTheme = (theme, { persist = false } = {}) => {
    const isLight = theme === "light";
    document.body.classList.toggle("theme-light", isLight);
    if (themeToggleBtn) themeToggleBtn.checked = !isLight;
    if (persist) localStorage.setItem(THEME_KEY, isLight ? "light" : "dark");
  };
  const savedThemeValue = localStorage.getItem(THEME_KEY);
  let theme;
  if (savedThemeValue === "light" || savedThemeValue === "dark") {
    theme = savedThemeValue; // explicit stored choice wins
  } else {
    const prefersLight =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-color-scheme: light)").matches;
    theme = prefersLight ? "light" : "dark"; // seed from OS; do not persist
  }
  applyTheme(theme);
  if (themeToggleBtn) {
    themeToggleBtn.checked = theme !== "light";
    themeToggleBtn.addEventListener("change", () => {
      applyTheme(themeToggleBtn.checked ? "dark" : "light", { persist: true });
    });
  }
}

function forceCachedForNonDev() {
  if (config.showDevSettings) return;
  state.showCachedBadges = true;
  try {
    localStorage.setItem(SHOW_CACHED_KEY, "true");
  } catch (e) {
    // storage may be unavailable; state default already covers it
  }
  const cachedToggle = document.getElementById("show-cached-toggle");
  if (cachedToggle) cachedToggle.checked = true;
}

function initChrome() {
  const scrapePanel = document.getElementById("scrape-panel");
  const scrapePanelBody = document.getElementById("scrape-panel-body");
  if (scrapePanel && scrapePanelBody) {
    scrapePanelBody.hidden = !scrapePanel.open;
    scrapePanel.addEventListener("toggle", () => {
      scrapePanelBody.hidden = !scrapePanel.open;
    });
  }

  // The activity strip's "Details" disclosure (WP-22 · UIR-23): collapsed +
  // empty by default; the streamed raw log flows into it for power users.
  const detailsToggle = document.getElementById("status-toggle");
  if (detailsToggle) {
    detailsToggle.addEventListener("click", () => toggleDetails());
  }

  const sidebar = document.querySelector("aside");
  const calendarCard = document.getElementById("calendar-card");
  const backToTopBtn = document.getElementById("back-to-top");
  if (backToTopBtn) {
    backToTopBtn.addEventListener("click", () => {
      if (sidebar && typeof sidebar.scrollTo === "function") {
        sidebar.scrollTo({ top: 0, behavior: "auto" });
      } else {
        window.scrollTo({ top: 0, behavior: "auto" });
      }
    });
  }
  if (sidebar && calendarCard && backToTopBtn) {
    const toggleBackToTop = (visible) => backToTopBtn.classList.toggle("is-visible", !visible);
    if ("IntersectionObserver" in window) {
      const observer = new IntersectionObserver(
        (entries) => entries.forEach((entry) => toggleBackToTop(entry.isIntersecting)),
        { root: sidebar, threshold: 0.2 },
      );
      observer.observe(calendarCard);
    } else {
      const updateBackToTop = () => {
        const sidebarRect = sidebar.getBoundingClientRect();
        const cardRect = calendarCard.getBoundingClientRect();
        toggleBackToTop(cardRect.bottom > sidebarRect.top && cardRect.top < sidebarRect.bottom);
      };
      sidebar.addEventListener("scroll", updateBackToTop);
      window.addEventListener("resize", updateBackToTop);
      updateBackToTop();
    }
  }
}

async function initData() {
  try {
    setLoading("Loading releases…");
    await fetchReleases();
  } catch (err) {
    console.warn(err);
    showError((err && err.message) || "Failed to load releases. Is the bcfeed proxy running?");
    return;
  }
  try {
    const [viewed, starred] = await Promise.all([loadViewedSet(), loadStarredSet()]);
    state.viewed = viewed || new Set();
    state.starred = starred || new Set();
  } catch (err) {
    // Degrade gracefully: the table is still usable without seen/starred
    // history — default to empty sets and say so instead of hiding the app.
    console.warn("Failed to load seen/starred state", err);
    state.viewed = new Set();
    state.starred = new Set();
    showNotice(
      "Couldn't load your seen and starred history — everything is shown as new. Reload the page to try again.",
    );
  }
  setDefaultDateFilters();
  renderTable();
  renderCalendar("range");
  refreshToggleButtons();
  fetchScrapeStatus();
  hideLoading();
}

async function main() {
  await initConfig();
  initEndpoints();
  // Instrumentation seam the Playwright smoke reads to assert the render-storm
  // fixes (mark-all-seen ≤ 2 renders; a single toggle rebuilds no table).
  window.__bcfeedRenderCounts = renderCounts;
  setRenderHandlers(renderTable, () => renderCalendar("range"));

  applyDevSettingsVisibility();
  initTheme();
  forceCachedForNonDev();

  initModals();
  initTable();
  initSettings();
  initPopulate();
  initCalendar();
  initChrome();

  // Server-down / recovery (WP-22 · UXP-20): a non-blocking banner instead of a
  // latching modal. The table stays browsable; mutating controls disable with
  // an explanation; reconnection auto-dismisses the banner and toasts recovery.
  setConnectionHandlers({
    onDown: () => {
      state.serverOffline = true;
      setControlsOffline(true);
      updatePopulateButton();
      updateHeaderRange();
      showBanner(
        "server-down",
        "bcfeed isn't running. Start it from Terminal — this page reconnects on its own.",
        { kind: "warn", dismissible: false },
      );
    },
    onUp: () => {
      state.serverOffline = false;
      dismissBanner("server-down");
      setControlsOffline(false);
      updatePopulateButton();
      updateHeaderRange();
      refreshToggleButtons();
      showToast("Reconnected.", { kind: "success" });
    },
  });

  setTimeout(() => checkServerAlive(), 500);
  setInterval(() => checkServerAlive(), 5000);

  await initData();
}

main();
