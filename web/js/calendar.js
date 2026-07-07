// Calendar-as-coverage-map: month grid, range selection, and the scrape-status
// sync that paints coverage + unseen dots (WP-18 · ARC-4/ARCH-5).

import {
  state,
  releases,
  releaseKey,
  scrapeStatus,
  renderCounts,
  formatDate,
  parseDateString,
  isoKeyFromDate,
  getLastSelectableDate,
} from "./state.js";
import { csrfFetch as fetch } from "./config.js";
import { endpoints } from "./api.js";
import { renderTable } from "./table.js";
import { updateStatusForDateFilter, updateHeaderRange } from "./status.js";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
];
const ARROW_KEYS = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"];
// Today is deliberately never selectable — its notification emails aren't final
// yet (a core invariant). Explain that on the cell instead of leaving a silent
// grey square (WP-25 · UXP-18).
const TODAY_TOOLTIP = "Today's emails are still arriving — check back tomorrow.";
// The day cell that owns the grid's single tab stop (roving tabindex). Persisted
// across re-renders so keyboard focus lands where the user left it (WP-21/JS-7).
let activeCellKey = null;
const calendarRange = document.getElementById("calendar-range");
const calendarRangeMonth = document.getElementById("calendar-range-month");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");
const filterByDateToggle = document.getElementById("filter-by-date");
const scrapePanel = document.getElementById("scrape-panel");
const selectMonthBtn = document.getElementById("select-month-btn");
const CALENDAR_STATE_KEY = "bc_calendar_state_v1";

const calendars = {
  range: { container: calendarRange, current: new Date(), startKey: null, endKey: null },
};

export function renderCalendar(type) {
  const cal = calendars[type];
  if (!cal || !cal.container) return;
  renderCounts.calendar += 1;
  const grid = cal.container;
  grid.innerHTML = "";
  // Real keyboard grid: container is role=grid, weekday cells are columnheaders,
  // day cells are focusable gridcell buttons driven by arrow keys (JS-7/UI-12).
  grid.setAttribute("role", "grid");
  grid.setAttribute("aria-label", "Calendar — choose a date range");

  if (calendarRangeMonth) {
    calendarRangeMonth.textContent = cal.current.toLocaleString("en-US", {
      month: "short",
      year: "numeric",
    });
  }

  WEEKDAYS.forEach((day, i) => {
    const label = document.createElement("div");
    label.className = "calendar-weekday";
    label.setAttribute("role", "columnheader");
    label.setAttribute("aria-label", WEEKDAY_NAMES[i]);
    label.textContent = day;
    grid.appendChild(label);
  });

  const startOffset = new Date(cal.current.getFullYear(), cal.current.getMonth(), 1).getDay();
  const totalCells = 42; // 6 weeks
  const lastSelectable = getLastSelectableDate();
  const todayKey = isoKeyFromDate(new Date());
  const startSelectedDate = cal.startKey ? parseDateString(cal.startKey) : null;
  const endSelectedDate = cal.endKey ? parseDateString(cal.endKey) : null;

  // Day → unseen-release count, computed ONCE per render instead of scanning
  // the whole releases array for every one of the 42 cells (JS-11/PERF-1).
  const unseenByDay = new Map();
  releases.forEach((rel) => {
    if (state.viewed.has(releaseKey(rel))) return;
    const relDate = formatDate(rel.date);
    if (!relDate) return;
    unseenByDay.set(relDate, (unseenByDay.get(relDate) || 0) + 1);
  });

  for (let idx = 0; idx < totalCells; idx++) {
    const dayNumber = idx - startOffset + 1;
    const cellDate = new Date(cal.current.getFullYear(), cal.current.getMonth(), dayNumber);
    const isOtherMonth = cellDate.getMonth() !== cal.current.getMonth();
    const key = isoKeyFromDate(cellDate);
    const cell = document.createElement("button");
    cell.type = "button";
    cell.className = "calendar-day";
    cell.setAttribute("role", "gridcell");
    cell.dataset.idx = String(idx);
    cell.dataset.key = key;
    const isDisabled = cellDate > lastSelectable;
    if (isOtherMonth) cell.classList.add("other-month");
    if (isDisabled) cell.classList.add("disabled");
    const isSelected = cal.startKey === key || cal.endKey === key;
    if (isSelected) cell.classList.add("selected");
    const inRange =
      startSelectedDate &&
      endSelectedDate &&
      cellDate >= startSelectedDate &&
      cellDate <= endSelectedDate;
    if (inRange) cell.classList.add("in-range");
    const isScraped = scrapeStatus.scraped.has(key);
    if (isScraped) cell.classList.add("populated-day");
    // The actionable gap (WP-25 · UXP-17): a day INSIDE the selection that has
    // not been checked. It is the loudest cell on the grid — the one state that
    // needs an action — mirrored by the "N dates not checked" summary line.
    const isGap = (isSelected || inRange) && !isScraped && !isDisabled && !isOtherMonth;
    if (isGap) cell.classList.add("gap");

    const dateLabel = document.createElement("span");
    dateLabel.className = "date-label";
    dateLabel.textContent = String(cellDate.getDate());
    cell.appendChild(dateLabel);
    const dots = document.createElement("div");
    dots.className = "dot-strip";
    const hasUnseen = unseenByDay.has(key);
    if (hasUnseen) {
      const dot = document.createElement("span");
      dot.className = "dot unseen";
      dots.appendChild(dot);
    }
    cell.appendChild(dots);

    if (isOtherMonth) {
      // Empty placeholders — hidden from AT and unfocusable.
      cell.setAttribute("aria-hidden", "true");
      cell.tabIndex = -1;
    } else {
      // Accessible name announces the full date + coverage/unseen state, so a
      // screen reader hears "June 20, has new releases, checked" (UXP-17).
      const monthLong = cellDate.toLocaleString("en-US", { month: "long" });
      let label = `${monthLong} ${cellDate.getDate()}, ${cellDate.getFullYear()}`;
      if (hasUnseen) label += ", has new releases";
      if (isScraped) label += ", checked";
      else if (isGap) label += ", not checked yet";
      if (key === todayKey) {
        // Name the today-exclusion in the accessible name and a hover tooltip.
        cell.title = TODAY_TOOLTIP;
        label += ", today — still receiving emails, not selectable yet";
      } else if (isDisabled) {
        label += ", unavailable";
      }
      cell.setAttribute("aria-label", label);
      if (isDisabled) {
        cell.setAttribute("aria-disabled", "true");
        cell.tabIndex = -1;
      } else {
        cell.setAttribute("aria-pressed", String(!!(isSelected || inRange)));
        cell.tabIndex = -1; // roving; the active cell is promoted to 0 below
      }
    }

    cell.addEventListener("click", (evt) => {
      if (isDisabled || isOtherMonth) return;
      activeCellKey = key;
      applyDaySelection(cal, key, evt.shiftKey);
    });
    grid.appendChild(cell);
  }

  // Roving tabindex: exactly one focusable day owns the grid's tab stop.
  const focusable = Array.from(
    grid.querySelectorAll(".calendar-day:not(.other-month):not(.disabled)"),
  );
  let active = focusable.find((b) => b.dataset.key === activeCellKey);
  if (!active) active = focusable.find((b) => b.classList.contains("selected")) || focusable[0];
  if (active) {
    active.tabIndex = 0;
    activeCellKey = active.dataset.key;
  }
}

// Shared range-selection logic used by both pointer clicks and keyboard
// Enter/Space, so the two paths never diverge.
function applyDaySelection(cal, clickedKey, shiftKey) {
  if (shiftKey) {
    const startKey = cal.startKey || cal.endKey;
    const endKey = cal.endKey || cal.startKey;
    const baseStart = startKey ? parseDateString(startKey) : null;
    const baseEnd = endKey ? parseDateString(endKey) : null;
    const clickedDate = parseDateString(clickedKey);
    if (baseStart && baseEnd && clickedDate) {
      const newStart = baseStart < baseEnd ? baseStart : baseEnd;
      const newEnd = baseStart < baseEnd ? baseEnd : baseStart;
      if (clickedDate < newStart) {
        cal.startKey = isoKeyFromDate(clickedDate);
        cal.endKey = isoKeyFromDate(newEnd);
      } else if (clickedDate > newEnd) {
        cal.startKey = isoKeyFromDate(newStart);
        cal.endKey = isoKeyFromDate(clickedDate);
      } else {
        // clicked inside range → collapse to single day
        cal.startKey = clickedKey;
        cal.endKey = null;
      }
    } else if (cal.startKey) {
      cal.endKey = clickedKey;
      const s = parseDateString(cal.startKey);
      const e = parseDateString(cal.endKey);
      if (s && e && e < s) {
        cal.endKey = cal.startKey;
        cal.startKey = clickedKey;
      }
    } else {
      cal.startKey = clickedKey;
      cal.endKey = null;
    }
  } else {
    cal.startKey = clickedKey;
    cal.endKey = null;
  }
  renderCalendar("range");
  applyCalendarFiltersFromSelection();
}

// Keyboard grid navigation: arrows move focus (roving tabindex), Enter/Space
// select, Home/End jump within the visual week row. Attached once to the grid
// container, which survives the innerHTML rebuilds (WP-21/JS-7).
function onGridKeydown(evt) {
  const cell = evt.target.closest && evt.target.closest(".calendar-day");
  if (!cell || !calendarRange.contains(cell)) return;

  if (evt.key === "Enter" || evt.key === " " || evt.key === "Spacebar" || evt.key === "Space") {
    if (cell.classList.contains("disabled") || cell.classList.contains("other-month")) return;
    evt.preventDefault();
    activeCellKey = cell.dataset.key;
    applyDaySelection(calendars.range, cell.dataset.key, evt.shiftKey);
    focusActiveCell();
    return;
  }
  if (!ARROW_KEYS.includes(evt.key)) return;
  evt.preventDefault();

  const focusable = Array.from(
    calendarRange.querySelectorAll(".calendar-day:not(.other-month):not(.disabled)"),
  );
  const domIdx = focusable.indexOf(cell);
  if (domIdx === -1) return;
  const gridIdx = Number(cell.dataset.idx);
  const row = Math.floor(gridIdx / 7);
  const byGridIdx = (n) => focusable.find((b) => Number(b.dataset.idx) === n) || null;
  let target = null;
  switch (evt.key) {
    case "ArrowRight":
      target = focusable[domIdx + 1] || null;
      break;
    case "ArrowLeft":
      target = focusable[domIdx - 1] || null;
      break;
    case "ArrowDown":
      target = byGridIdx(gridIdx + 7);
      break;
    case "ArrowUp":
      target = byGridIdx(gridIdx - 7);
      break;
    case "Home":
      target = focusable.find((b) => Math.floor(Number(b.dataset.idx) / 7) === row) || null;
      break;
    case "End":
      target =
        [...focusable].reverse().find((b) => Math.floor(Number(b.dataset.idx) / 7) === row) || null;
      break;
    default:
      break;
  }
  if (!target) return;
  cell.tabIndex = -1;
  target.tabIndex = 0;
  activeCellKey = target.dataset.key;
  target.focus();
}

function focusActiveCell() {
  const active = calendarRange.querySelector(
    `.calendar-day[data-key="${CSS.escape(activeCellKey)}"]`,
  );
  if (
    active &&
    !active.classList.contains("disabled") &&
    !active.classList.contains("other-month")
  ) {
    active.focus();
  }
}

function shiftCalendarMonth(type, delta) {
  const cal = calendars[type];
  if (!cal) return;
  const next = new Date(cal.current.getFullYear(), cal.current.getMonth() + delta, 1);
  const now = new Date();
  const maxMonth = new Date(now.getFullYear(), now.getMonth(), 1);
  cal.current = next > maxMonth ? maxMonth : next;
  renderCalendar(type);
}

export function initializeCalendars() {
  const dateValues = releases
    .map((entry) => parseDateString(entry.date))
    .filter(Boolean)
    .sort((a, b) => a - b);
  const today = new Date();
  if (dateValues.length) {
    calendars.range.current = new Date(dateValues[0].getFullYear(), dateValues[0].getMonth(), 1);
  } else {
    calendars.range.current = new Date(today.getFullYear(), today.getMonth(), 1);
  }
  syncCalendarsFromInputs();
  renderCalendar("range");
}

function syncCalendarsFromInputs() {
  const fromVal = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  const toVal = dateFilterTo ? dateFilterTo.value.trim() : "";
  const parsedFrom = parseDateString(fromVal);
  const parsedTo = parseDateString(toVal);
  calendars.range.startKey = parsedFrom ? isoKeyFromDate(parsedFrom) : null;
  calendars.range.endKey = parsedTo ? isoKeyFromDate(parsedTo) : null;
  const target = parsedTo || parsedFrom;
  if (target) {
    calendars.range.current = new Date(target.getFullYear(), target.getMonth(), 1);
  }
}

function selectVisibleMonthRange() {
  const cal = calendars.range;
  if (!cal) return;
  const current = cal.current || new Date();
  const lastSelectable = getLastSelectableDate();
  let year = current.getFullYear();
  let month = current.getMonth();
  let startDate = new Date(year, month, 1);
  if (lastSelectable < startDate) {
    year = lastSelectable.getFullYear();
    month = lastSelectable.getMonth();
    startDate = new Date(year, month, 1);
  }
  let endDate = new Date(year, month + 1, 0);
  if (
    lastSelectable.getFullYear() === year &&
    lastSelectable.getMonth() === month &&
    lastSelectable < endDate
  ) {
    endDate = new Date(lastSelectable);
  }
  cal.startKey = isoKeyFromDate(startDate);
  cal.endKey = isoKeyFromDate(endDate);
  renderCalendar("range");
  applyCalendarFiltersFromSelection();
}

export function applyCalendarFiltersFromSelection() {
  const cal = calendars.range;
  const fromKey = cal.startKey;
  const toKey = cal.endKey || "";
  if (dateFilterFrom && fromKey) {
    dateFilterFrom.value = fromKey;
  }
  if (dateFilterTo) {
    dateFilterTo.value = toKey;
  }
  onDateFilterChange();
  updateStatusForDateFilter();
}

export function onDateFilterChange() {
  state.dateFilterFrom = dateFilterFrom ? dateFilterFrom.value.trim() : "";
  state.dateFilterTo = dateFilterTo ? dateFilterTo.value.trim() : "";
  syncCalendarsFromInputs();
  renderCalendar("range");
  updateStatusForDateFilter();
  updateHeaderRange();
  persistCalendarState();
  renderTable();
}

export async function fetchScrapeStatus() {
  if (!endpoints.apiRoot) return;
  try {
    const params = new URLSearchParams();
    const startDate = state.dateFilterFrom || state.dateFilterTo || formatDate(releases[0]?.date);
    const endDate =
      state.dateFilterTo || state.dateFilterFrom || formatDate(releases[releases.length - 1]?.date);
    if (startDate) params.set("start", startDate);
    if (endDate) params.set("end", endDate);
    const resp = await fetch(`${endpoints.apiRoot}/scrape-status?${params.toString()}`);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    scrapeStatus.scraped = new Set(data.scraped || []);
    scrapeStatus.notScraped = new Set(data.not_scraped || []);
    renderCalendar("range");
    updateStatusForDateFilter();
    updateHeaderRange();
  } catch (err) {
    console.warn("Failed to load scrape status", err);
  }
}

function loadCalendarState() {
  if (!dateFilterFrom || !dateFilterTo) return;
  try {
    const raw = localStorage.getItem(CALENDAR_STATE_KEY);
    if (!raw) return;
    const data = JSON.parse(raw);
    if (data && typeof data === "object") {
      if (typeof data.from === "string") dateFilterFrom.value = data.from;
      if (typeof data.to === "string") dateFilterTo.value = data.to;
    }
  } catch (err) {
    // ignore malformed stored state
  }
  onDateFilterChange();
}

function persistCalendarState() {
  if (!dateFilterFrom || !dateFilterTo) return;
  const payload = {
    from: (dateFilterFrom.value || "").trim(),
    to: (dateFilterTo.value || "").trim(),
  };
  try {
    localStorage.setItem(CALENDAR_STATE_KEY, JSON.stringify(payload));
  } catch (err) {
    // ignore storage failures
  }
}

export function setDefaultDateFilters() {
  if ((dateFilterFrom && dateFilterFrom.value) || (dateFilterTo && dateFilterTo.value)) return;
  if (!releases.length) return;
  const dates = releases
    .map((entry) => parseDateString(entry.date))
    .filter(Boolean)
    .sort((a, b) => a - b);
  if (!dates.length) return;
  const first = isoKeyFromDate(dates[0]);
  const last = isoKeyFromDate(dates[dates.length - 1]);
  if (dateFilterFrom && !dateFilterFrom.value) dateFilterFrom.value = first;
  if (dateFilterTo && !dateFilterTo.value) dateFilterTo.value = last;
  onDateFilterChange();
}

function updateDateFilterUi() {
  if (!scrapePanel) return;
  scrapePanel.classList.toggle("calendar-disabled", !state.filterByDate);
}

// Wire the calendar's one-time listeners; called once by main.js.
export function initCalendar() {
  document.querySelectorAll("[data-cal-nav]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const role = btn.getAttribute("data-cal-nav") || "";
      if (role.startsWith("range")) shiftCalendarMonth("range", role.endsWith("prev") ? -1 : 1);
    });
  });

  document.querySelectorAll("[data-cal-today]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const cal = calendars.range;
      if (!cal) return;
      const lastSelectable = getLastSelectableDate();
      const lastKey = isoKeyFromDate(lastSelectable);
      cal.current = new Date(lastSelectable.getFullYear(), lastSelectable.getMonth(), 1);
      if (!cal.startKey || (cal.startKey && cal.endKey)) {
        cal.startKey = lastKey;
        cal.endKey = null;
      } else {
        cal.endKey = lastKey;
      }
      renderCalendar("range");
      applyCalendarFiltersFromSelection();
    });
  });

  if (calendarRange) calendarRange.addEventListener("keydown", onGridKeydown);

  if (selectMonthBtn) selectMonthBtn.addEventListener("click", selectVisibleMonthRange);
  if (dateFilterFrom) dateFilterFrom.addEventListener("input", onDateFilterChange);
  if (dateFilterTo) dateFilterTo.addEventListener("input", onDateFilterChange);

  if (filterByDateToggle) {
    filterByDateToggle.checked = state.filterByDate;
    updateDateFilterUi();
    filterByDateToggle.addEventListener("change", () => {
      state.filterByDate = !!filterByDateToggle.checked;
      updateDateFilterUi();
      renderTable();
      updateStatusForDateFilter();
    });
  }

  initializeCalendars();
  loadCalendarState();
}
