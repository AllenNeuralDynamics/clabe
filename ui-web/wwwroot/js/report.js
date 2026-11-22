// Browser helpers of the hub's report viewer and fleet pages.

/** Scrolls `container` so that row `index` (each `rowHeight` px, after `top` px of header) is centered. */
export function scrollToRow(container, index, rowHeight, top) {
  if (!container) return;
  const y = (top || 0) + index * rowHeight - container.clientHeight / 2 + rowHeight / 2;
  container.scrollTop = Math.max(0, y);
}

/** Scrolls the first element matching `selector` inside `container` into view (centered when it was hidden). */
export function revealIn(container, selector) {
  if (!container) return;
  const el = container.querySelector(selector);
  if (!el) return;
  const box = container.getBoundingClientRect();
  const r = el.getBoundingClientRect();
  const top = Math.max(box.top, 0) + 32;
  const bottom = Math.min(box.bottom, window.innerHeight) - 8;
  if (r.top < top || r.bottom > bottom) el.scrollIntoView({ block: "center", inline: "nearest" });
}

/** Focuses an element (and selects its text, for inputs). */
export function focusSelect(el) {
  if (!el) return;
  el.focus();
  if (typeof el.select === "function") el.select();
}

/** Opens the file dialog of a hidden file input. */
export function pick(el) {
  if (el) el.click();
}
