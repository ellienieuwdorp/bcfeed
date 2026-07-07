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
const calendarRange = document.getElementById("calendar-range");
const calendarRangeMonth = document.getElementById("calendar-range-month");
const dateFilterFrom = document.getElementById("date-filter-from");
const dateFilterTo = document.getElementById("date-filter-to");
const filterByDateToggle = document.getElementById("filter-by-date");
const scrapePanel = document.getElementById("scrape-wireframe");
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

  if (calendarRangeMonth) {
    calendarRangeMonth.textContent = cal.current.toLocaleString("en-US", {
      month: "short",
      year: "numeric",
    });
  }

  WEEKDAYS.forEach((day) => {
    const label = document.createElement("div");
    label.className = "calendar-weekday";
    label.textContent = day;
    grid.appendChild(label);
  });

  const startOffset = new Date(cal.current.getFullYear(), cal.current.getMonth(), 1).getDay();
  const totalCells = 42; // 6 weeks
  const lastSelectable = getLastSelectableDate();
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
    const cell = document.createElement("div");
    cell.className = "calendar-day";
    const isDisabled = cellDate > lastSelectable;
    if (isOtherMonth) cell.classList.add("other-month");
    if (isDisabled) cell.classList.add("disabled");
    if (cal.startKey === key || cal.endKey === key) cell.classList.add("selected");
    if (
      startSelectedDate &&
      endSelectedDate &&
      cellDate >= startSelectedDate &&
      cellDate <= endSelectedDate
    ) {
      cell.classList.add("in-range");
    }
    cell.textContent = "";
    const dateLabel = document.createElement("span");
    dateLabel.className = "date-label";
    dateLabel.textContent = String(cellDate.getDate());
    if (scrapeStatus.scraped.has(key)) {
      cell.classList.add("unseen-day");
    }
    cell.appendChild(dateLabel);
    const dots = document.createElement("div");
    dots.className = "dot-strip";
    if (unseenByDay.has(key)) {
      const dot = document.createElement("span");
      dot.className = "dot unseen";
      dot.style.background = "#ff5f5f";
      dot.style.borderColor = "rgba(0,0,0,0.25)";
      dots.appendChild(dot);
    }
    cell.appendChild(dots);

    cell.addEventListener("click", (evt) => {
      if (isDisabled) return;
      const clickedKey = key;
      if (evt.shiftKey) {
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
    });
    grid.appendChild(cell);
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
