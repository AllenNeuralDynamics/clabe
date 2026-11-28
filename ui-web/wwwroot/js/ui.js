// Browser helpers for the web UI: theme, keyboard shortcuts, tab title and favicon badge, downloads, full screen.

const THEME_KEY = "clabe.theme";

/** Applies the saved theme ("light", "dark" or "system") to <html data-theme>. */
export function applyTheme(choice) {
  const theme = choice ?? localStorage.getItem(THEME_KEY) ?? "system";
  if (choice) localStorage.setItem(THEME_KEY, choice);
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  return theme;
}
/** What this computer prefers: "dark" or "light" (what the "system" theme follows). */
export function systemTheme() {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => applyTheme());
applyTheme();

/** Whether keyboard focus is in a text field: plain letters and numbers then type, not trigger shortcuts. */
function typing(event) {
  const el = event.target;
  if (!(el instanceof HTMLElement)) return false;
  if (el.isContentEditable) return true;
  if (el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  if (el.tagName === "INPUT") {
    const type = (el.getAttribute("type") || "text").toLowerCase();
    return !["checkbox", "radio", "button", "submit", "range"].includes(type);
  }
  return false;
}

/** The shortcut name of a key event, e.g. "Alt+ArrowLeft", "Ctrl+K", "Ctrl+Shift+R", "r", "?", "F2". */
function keyName(event) {
  const parts = [];
  if (event.ctrlKey || event.metaKey) parts.push("Ctrl");
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey && event.key.length > 1) parts.push("Shift");
  if (event.shiftKey && event.key.length === 1 && /[a-z]/i.test(event.key) && (event.ctrlKey || event.metaKey)) {
    parts.push("Shift");
  }
  const key = event.key.length === 1 ? (event.ctrlKey || event.metaKey ? event.key.toUpperCase() : event.key) : event.key;
  parts.push(key);
  return parts.join("+");
}

/**
 * Sends shortcuts to .NET: `receiver.invokeMethodAsync("OnShortcut", name, typing)` returns true when it handled
 * the key (its default action is then prevented). Plain keys (letters, digits, "?") are only sent outside text
 * fields; combinations and function keys always are.
 */
export function listenShortcuts(receiver) {
  let altAlone = false;
  const handler = async (event) => {
    if (event.isComposing) return;
    altAlone = event.key === "Alt" && !event.ctrlKey && !event.shiftKey && !event.metaKey;
    const name = keyName(event);
    const plain = !event.ctrlKey && !event.metaKey && !event.altKey && event.key.length === 1;
    const inField = typing(event);
    if (plain && inField) return;
    if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return;
    if (name === "Enter" || name === "Shift+Enter") {
      if (enterInPlace(event)) return;
    }
    // Decide synchronously which keys we own, so the browser's default (e.g. Alt+← = history back) is stopped.
    const owned = OWNED.has(name) || (plain && OWNED_PLAIN.test(name))
      || (name === "Escape" && document.querySelector("[data-escape]"))
      || (!inField && ARROWS.has(name) && document.querySelector("[data-arrows]"));
    if (owned) event.preventDefault();
    const handled = await receiver.invokeMethodAsync("OnShortcut", name, inField);
    if (handled && !owned) event.preventDefault();
  };
  // Alt pressed and released alone opens the menu bar (desktop style).
  const altUp = (event) => {
    if (event.key !== "Alt" || !altAlone) return;
    altAlone = false;
    event.preventDefault();
    receiver.invokeMethodAsync("OnShortcut", "Alt", typing(event));
  };
  const blur = () => { altAlone = false; };
  window.addEventListener("keydown", handler, true);
  window.addEventListener("keyup", altUp, true);
  window.addEventListener("mousedown", blur, true);
  return {
    dispose: () => {
      window.removeEventListener("keydown", handler, true);
      window.removeEventListener("keyup", altUp, true);
      window.removeEventListener("mousedown", blur, true);
    },
  };
}
const OWNED = new Set([
  "Alt+ArrowLeft", "Alt+ArrowRight", "Ctrl+K", "Ctrl+S", "Ctrl+Shift+R", "F2", "Ctrl+Enter", "F5",
]);
const OWNED_PLAIN = /^[1-9ynr?]$/;
const ARROWS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Home", "End"]);

const FIELDS = "input:not([type=hidden]):not([disabled]):not([tabindex='-1']), select:not([disabled]), textarea:not([disabled])";

/**
 * Enter where it acts in place (returns true; .NET is not told): on a button, link or summary it clicks; in an open
 * combobox it picks (the combobox handles it); in a form field it moves to the next field, and on the last one
 * submits the form; Shift+Enter in a multi-line field is a newline.
 */
function enterInPlace(event) {
  const el = event.target;
  if (!(el instanceof HTMLElement)) return false;
  if (el.closest("button, a[href], summary, [role=option], [role=menuitem]")) return true;
  if (el.getAttribute("role") === "combobox" && el.getAttribute("aria-expanded") === "true") {
    event.preventDefault();
    return true;
  }
  if (el.closest("[role=dialog]")) {
    // a dialog's field (e.g. the path picker's, inside a prompt's form): its own handler, never the form's submit
    if (el.tagName !== "TEXTAREA" && el.closest("form")) event.preventDefault();
    return true;
  }
  const form = el.closest("form[data-enter-next]");
  if (!form || !el.matches(FIELDS)) return el.matches(FIELDS); // a field outside a prompt form: its own default
  if (event.shiftKey) {
    if (el.tagName === "TEXTAREA") return true; // a newline
    event.preventDefault();
    return true;
  }
  event.preventDefault();
  const fields = [...form.querySelectorAll(FIELDS)].filter((f) => f.offsetParent !== null);
  const next = fields[fields.indexOf(el) + 1];
  if (next) {
    next.focus();
    if (typeof next.select === "function" && next.type !== "range" && next.type !== "checkbox") next.select();
  } else {
    form.requestSubmit();
  }
  return true;
}

/** Submits the prompt's form (Ctrl+Enter, F5), as if its submit button was pressed. */
export function submitForm(selector) {
  const form = document.querySelector(selector);
  if (!form) return false;
  form.requestSubmit();
  return true;
}

/** Opens `url` in a new tab. */
export function openTab(url) {
  window.open(url, "_blank", "noopener");
}

/** Shows "(n) title" in the tab and a dot on the favicon while a prompt waits. */
export function setAttention(title, waiting) {
  document.title = waiting ? `(${waiting}) ${title}` : title;
  const link = document.querySelector("link[rel=icon]");
  if (!link) return;
  if (!link.dataset.base) link.dataset.base = link.href;
  if (!waiting) {
    link.href = link.dataset.base;
    return;
  }
  const img = new Image();
  img.onload = () => {
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 64;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(img, 0, 0, 64, 64);
    ctx.fillStyle = "#c2410c";
    ctx.beginPath();
    ctx.arc(50, 14, 13, 0, 2 * Math.PI);
    ctx.fill();
    link.href = canvas.toDataURL("image/png");
  };
  img.src = link.dataset.base;
}

/** Downloads `url` (same origin; the token goes in the query) as a file. */
export function download(url) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Toggles full screen. */
export async function toggleFullScreen() {
  if (document.fullscreenElement) await document.exitFullscreen();
  else await document.documentElement.requestFullscreen();
}

/** Focuses the first element matching `selector` (e.g. the prompt's first input). */
export function focus(selector) {
  const el = document.querySelector(selector);
  if (el) el.focus();
}

/** Scrolls the element matching `selector` into view. */
export function reveal(selector) {
  const el = document.querySelector(selector);
  if (el) el.scrollIntoView({ block: "nearest" });
}
