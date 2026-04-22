let renderHook = () => {};

export function registerRenderHook(fn) {
  renderHook = typeof fn === "function" ? fn : () => {};
}

export function requestRender() {
  renderHook();
}
