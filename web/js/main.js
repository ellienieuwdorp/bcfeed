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
} from "./api.js";
import { state, setRenderHandlers, renderCounts } from "./state.js";
import { setLoading, hideLoading, showError, showNotice, logReplace } from "./status.js";
import { renderTable, refreshToggleButtons, initTable } from "./table.js";
import {
  renderCalendar,
  initCalendar,
  setDefaultDateFilters,
  fetchScrapeStatus,
} from "./calendar.js";
import { initModals } from "./modals.js";
import { initPopulate } from "./populate.js";
import { initSettings } from "./settings.js";

const THEME_KEY = "bc_dashboard_theme";
const SHOW_CACHED_KEY = "bc_show_cached_badges";
const themeToggleBtn = document.getElementById("theme-toggle");

function applyDevSettingsVisibility() {
  document.querySelectorAll(".dev-setting").forEach((el) => {
    el.style.display = config.showDevSettings ? "" : "none";
  });
}

function initTheme() {
  const applyTheme = (theme) => {
    const isLight = theme === "light";
    document.body.classList.toggle("theme-light", isLight);
    if (themeToggleBtn) themeToggleBtn.checked = !isLight;
    localStorage.setItem(THEME_KEY, isLight ? "light" : "dark");
  };
  const savedThemeValue = localStorage.getItem(THEME_KEY);
  let savedTheme = savedThemeValue || config.defaultTheme || "light";
  if (!config.showDevSettings && !savedThemeValue) {
    savedTheme = "dark";
  }
  applyTheme(savedTheme);
  if (themeToggleBtn) {
    themeToggleBtn.checked = savedTheme !== "light";
    themeToggleBtn.addEventListener("change", () => {
      applyTheme(themeToggleBtn.checked ? "dark" : "light");
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
  const scrapePanel = document.getElementById("scrape-wireframe");
  const scrapePanelBody = document.getElementById("scrape-wireframe-body");
  if (scrapePanel && scrapePanelBody) {
    scrapePanelBody.hidden = !scrapePanel.open;
    scrapePanel.addEventListener("toggle", () => {
      scrapePanelBody.hidden = !scrapePanel.open;
    });
  }

  const statusLogCard = document.querySelector(".calendar-log");
  const statusToggleBtn = document.getElementById("status-toggle");
  if (statusLogCard && statusToggleBtn) {
    statusLogCard.classList.remove("collapsed");
    statusToggleBtn.setAttribute("aria-expanded", "true");
    statusToggleBtn.addEventListener("click", () => {
      const isCollapsed = statusLogCard.classList.toggle("collapsed");
      statusToggleBtn.setAttribute("aria-expanded", String(!isCollapsed));
    });
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
  if (config.clearStatusOnLoad) {
    // Route through the single status-log writer (status.js).
    logReplace("Select a date range to display.");
  }
  initTheme();
  forceCachedForNonDev();

  initModals();
  initTable();
  initSettings();
  initPopulate();
  initCalendar();
  initChrome();

  setTimeout(() => checkServerAlive(), 500);
  setInterval(() => checkServerAlive(), 5000);

  await initData();
}

main();
