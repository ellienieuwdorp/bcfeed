// Label/page filter list (WP-18 · ARC-4/ARCH-5).
//
// Split out of table.js to keep both modules well under the size budget. Builds
// the sidebar's show / show-only checkbox list and coordinates their state; a
// checkbox change schedules a coalesced table re-render.

import { state, releases, scheduleRender } from "./state.js";

let lastLabelSignature = "";

export function syncShowCheckboxAvailability() {
  const disableShow = state.showOnlyLabels.size > 0;
  document.querySelectorAll("#label-filters .filter-item").forEach((item) => {
    const show = item.querySelector('input[data-filter-role="show"]');
    if (show) {
      show.disabled = disableShow;
    }
    item.classList.toggle("show-only-active", disableShow);
  });
}

export function renderFilters(sourceList = releases) {
  const counts = sourceList.reduce((acc, r) => {
    if (!r.page_name) return acc;
    acc[r.page_name] = (acc[r.page_name] || 0) + 1;
    return acc;
  }, {});
  const labels = Object.keys(counts).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
  const container = document.getElementById("label-filters");
  container.innerHTML = "";

  if (labels.length === 0) {
    container.innerHTML = "<div class='detail-meta'>No label/page data available.</div>";
    return;
  }

  const labelSignature = labels.join("||");
  if (state.showOnlyLabels.size === 0 && labelSignature !== lastLabelSignature) {
    state.showLabels = new Set(labels);
  } else if (state.showLabels.size === 0) {
    labels.forEach((label) => state.showLabels.add(label));
  }
  lastLabelSignature = labelSignature;

  const showOnlyMode = state.showOnlyLabels.size > 0;

  labels.forEach((label) => {
    const wrapper = document.createElement("div");
    wrapper.className = "filter-item";
    if (showOnlyMode) wrapper.classList.add("show-only-active");

    const showCheckbox = document.createElement("input");
    showCheckbox.type = "checkbox";
    showCheckbox.className = "filter-checkbox show";
    showCheckbox.dataset.filterRole = "show";
    showCheckbox.checked = state.showLabels.has(label);
    showCheckbox.disabled = showOnlyMode;
    showCheckbox.addEventListener("change", () => {
      if (showCheckbox.checked) {
        state.showLabels.add(label);
      } else {
        state.showLabels.delete(label);
      }
      scheduleRender({ table: true });
    });

    const showOnlyCheckbox = document.createElement("input");
    showOnlyCheckbox.type = "checkbox";
    showOnlyCheckbox.className = "filter-checkbox show-only";
    showOnlyCheckbox.dataset.filterRole = "show-only";
    showOnlyCheckbox.checked = state.showOnlyLabels.has(label);
    showOnlyCheckbox.addEventListener("change", () => {
      if (showOnlyCheckbox.checked) {
        state.showOnlyLabels.add(label);
      } else {
        state.showOnlyLabels.delete(label);
      }
      syncShowCheckboxAvailability();
      scheduleRender({ table: true });
    });

    const text = document.createElement("span");
    text.textContent = label;
    const count = document.createElement("span");
    count.className = "filter-count";
    count.textContent = `(${counts[label]})`;
    wrapper.appendChild(showCheckbox);
    wrapper.appendChild(showOnlyCheckbox);
    wrapper.appendChild(text);
    wrapper.appendChild(count);
    container.appendChild(wrapper);
  });

  syncShowCheckboxAvailability();
}
