import { state } from "../core/state.js";
import { renderCsvList, renderCsvViewer } from "../processes/exports/service.js";
import { destroyAllCharts, mountPendingCharts } from "../core/charts.js";
import { renderInspector } from "../processes/inspector/service.js";
import { applySidebarView } from "../processes/navigation/sidebar.js";
import { renderConversationList } from "../processes/conversations/sidebar.js";
import { renderProviderControls, renderSessionHeader, renderThread } from "../processes/chat/thread.js";

function renderMain() {
  const mainEl = document.querySelector(".main");
  const viewingCsv = state.sidebarView === "csvs" && state.activeCsvId;
  mainEl.classList.toggle("viewing-csv", Boolean(viewingCsv));
  if (viewingCsv) {
    renderCsvViewer();
  } else {
    renderThread();
  }
}

export function render() {
  destroyAllCharts();
  state.pendingCharts.clear();

  applySidebarView();
  if (state.sidebarView === "csvs") {
    renderCsvList();
  } else {
    renderConversationList();
  }
  renderProviderControls();
  renderSessionHeader();
  renderMain();
  renderInspector();
  document.getElementById("sendBtn").disabled = state.isStreaming;
  mountPendingCharts(document);
}
