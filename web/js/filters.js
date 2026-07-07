// Label/page filter list (WP-18 · ARC-4/ARCH-5; WP-25 · UXP-15/UX-8/JS-5).
//
// The confusing dual show / show-only checkbox pair is gone. This is now a
// single faceted-search list: ONE checkbox per label (checked = included), a
// hover/focus-revealed "only" affordance per row, and All / None controls at
// the top. State is modelled as an EXCLUSION set (state.hiddenLabels): checking
// removes a label from it, unchecking adds it. Because exclusions persist and
// re-render only READS them (never rewrites them), unchecked labels stay
// unchecked across month/range navigation (the JS-5 fix), and an empty
// selection yields an empty table + the filtered-empty state — never an
// auto-reset-to-all.

import { state, releases, scheduleRender } from "./state.js";

const allBtn = document.getElementById("filter-all");
const noneBtn = document.getElementById("filter-none");

// The label universe of the currently-rendered list — cached so the All / None
// controls (wired once) operate on exactly the labels the panel shows.
let currentLabels = [];

function isShown(label) {
  return !state.hiddenLabels.has(label);
}

export function renderFilters(sourceList = releases) {
  const counts = sourceList.reduce((acc, r) => {
    if (!r.page_name) return acc;
    acc[r.page_name] = (acc[r.page_name] || 0) + 1;
    return acc;
  }, {});
  const labels = Object.keys(counts).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  currentLabels = labels;
  const container = document.getElementById("label-filters");
  container.innerHTML = "";

  if (labels.length === 0) {
    container.innerHTML = "<div class='detail-meta'>No label/page data available.</div>";
    refreshFilterControls();
    return;
  }

  labels.forEach((label) => {
    const wrapper = document.createElement("div");
    wrapper.className = "filter-item";

    const showCheckbox = document.createElement("input");
    showCheckbox.type = "checkbox";
    showCheckbox.className = "filter-checkbox show";
    showCheckbox.checked = isShown(label);
    showCheckbox.setAttribute("aria-label", `Show ${label}`);
    showCheckbox.addEventListener("change", () => {
      // Checked = included (remove from the exclusion set); unchecked = hidden.
      if (showCheckbox.checked) {
        state.hiddenLabels.delete(label);
      } else {
        state.hiddenLabels.add(label);
      }
      scheduleRender({ table: true });
    });

    const text = document.createElement("span");
    text.className = "filter-label";
    text.textContent = label;

    // The "only" affordance replaces the old show-only column: one click shows
    // exactly this label by excluding every other label currently in view.
    // Hidden until the row is hovered/focused (CSS), but always keyboard-
    // reachable via Tab.
    const only = document.createElement("button");
    only.type = "button";
    only.className = "filter-only";
    only.textContent = "only";
    only.setAttribute("aria-label", `Show only ${label}`);
    only.addEventListener("click", () => {
      state.hiddenLabels = new Set(currentLabels.filter((l) => l !== label));
      scheduleRender({ table: true });
    });

    const count = document.createElement("span");
    count.className = "filter-count";
    count.textContent = `(${counts[label]})`;

    wrapper.appendChild(showCheckbox);
    wrapper.appendChild(text);
    wrapper.appendChild(only);
    wrapper.appendChild(count);
    container.appendChild(wrapper);
  });

  refreshFilterControls();
}

// Enable/disable the All / None controls to match the current selection so they
// never look actionable when they would do nothing.
function refreshFilterControls() {
  const anyHidden = currentLabels.some((l) => state.hiddenLabels.has(l));
  const allHidden =
    currentLabels.length > 0 && currentLabels.every((l) => state.hiddenLabels.has(l));
  if (allBtn) allBtn.disabled = !anyHidden;
  if (noneBtn) noneBtn.disabled = allHidden || currentLabels.length === 0;
}

// Wire the All / None controls once (called by table.js initTable).
export function initFilters() {
  if (allBtn) {
    allBtn.addEventListener("click", () => {
      // Show everything: drop the exclusions for the labels in view. (Clearing
      // the whole set is equivalent here and keeps things simple.)
      state.hiddenLabels = new Set();
      scheduleRender({ table: true });
    });
  }
  if (noneBtn) {
    noneBtn.addEventListener("click", () => {
      // Hide every label currently in view → empty table + the filtered-empty
      // state (never an auto-reset-to-all).
      currentLabels.forEach((l) => state.hiddenLabels.add(l));
      scheduleRender({ table: true });
    });
  }
}
