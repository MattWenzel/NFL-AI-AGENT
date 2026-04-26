// Drag-to-resize for the sidebar (left) and inspector (right) panels.
// Width is bound to a CSS custom prop on :root, persisted in localStorage,
// and clamped to a min/max range. Disabled below 900px (mobile drawer).

const SIDEBAR = {
  storageKey: "nfl_sidebar_width",
  cssVar: "--sidebar-width",
  min: 200,
  max: 480,
  default: 280,
  side: "left",
};

const INSPECTOR = {
  storageKey: "nfl_inspector_width",
  cssVar: "--inspector-width",
  min: 280,
  max: 640,
  default: 380,
  side: "right",
};

const isMobile = () => window.matchMedia("(max-width: 900px)").matches;

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value));
}

function readStored(config) {
  const raw = localStorage.getItem(config.storageKey);
  if (!raw) return null;
  const n = parseInt(raw, 10);
  if (!Number.isFinite(n)) return null;
  return clamp(n, config.min, config.max);
}

function applyWidth(config, px) {
  document.documentElement.style.setProperty(config.cssVar, `${px}px`);
}

function attachResizer(handle, config) {
  if (!handle) return;

  const onPointerDown = (event) => {
    if (event.button !== 0 || isMobile()) return;
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    handle.classList.add("dragging");
    document.body.classList.add("is-resizing");

    const startX = event.clientX;
    const startWidth = parseInt(
      getComputedStyle(document.documentElement).getPropertyValue(config.cssVar),
      10,
    ) || config.default;

    const onMove = (ev) => {
      const dx = ev.clientX - startX;
      const next = config.side === "left" ? startWidth + dx : startWidth - dx;
      applyWidth(config, clamp(next, config.min, config.max));
    };

    const onUp = () => {
      handle.removeEventListener("pointermove", onMove);
      handle.removeEventListener("pointerup", onUp);
      handle.removeEventListener("pointercancel", onUp);
      handle.classList.remove("dragging");
      document.body.classList.remove("is-resizing");
      // Persist the committed width.
      const committed = parseInt(
        getComputedStyle(document.documentElement).getPropertyValue(config.cssVar),
        10,
      );
      if (Number.isFinite(committed)) {
        localStorage.setItem(config.storageKey, String(committed));
      }
    };

    handle.addEventListener("pointermove", onMove);
    handle.addEventListener("pointerup", onUp);
    handle.addEventListener("pointercancel", onUp);
  };

  handle.addEventListener("pointerdown", onPointerDown);

  // Keyboard nudges for accessibility — focus the handle, then arrow keys.
  handle.addEventListener("keydown", (event) => {
    if (isMobile()) return;
    const step = event.shiftKey ? 24 : 8;
    let delta = 0;
    if (event.key === "ArrowLeft") delta = config.side === "left" ? -step : step;
    else if (event.key === "ArrowRight") delta = config.side === "left" ? step : -step;
    else return;
    event.preventDefault();
    const current = parseInt(
      getComputedStyle(document.documentElement).getPropertyValue(config.cssVar),
      10,
    ) || config.default;
    const next = clamp(current + delta, config.min, config.max);
    applyWidth(config, next);
    localStorage.setItem(config.storageKey, String(next));
  });

  // Double-click to reset.
  handle.addEventListener("dblclick", () => {
    if (isMobile()) return;
    applyWidth(config, config.default);
    localStorage.removeItem(config.storageKey);
  });
}

export function initResizers() {
  // Restore saved widths before wiring handlers so the layout doesn't flash.
  const savedSidebar = readStored(SIDEBAR);
  if (savedSidebar !== null) applyWidth(SIDEBAR, savedSidebar);
  const savedInspector = readStored(INSPECTOR);
  if (savedInspector !== null) applyWidth(INSPECTOR, savedInspector);

  attachResizer(document.getElementById("sidebarResizer"), SIDEBAR);
  attachResizer(document.getElementById("inspectorResizer"), INSPECTOR);
}
