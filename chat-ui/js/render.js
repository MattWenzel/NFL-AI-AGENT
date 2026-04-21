import { state } from "./state.js";
import { renderCsvList, renderCsvViewer } from "./csv.js";
import { destroyAllCharts, mountPendingCharts } from "./charts.js";
import { renderInspector } from "./inspector.js";
import { applySidebarView } from "./navigation.js";
import { renderConversationList } from "./sidebar.js";
import { renderSessionHeader, renderThread } from "./thread.js";

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
  renderSessionHeader();
  renderMain();
  renderInspector();
  document.getElementById("sendBtn").disabled = state.isStreaming;
  mountPendingCharts(document);
}
