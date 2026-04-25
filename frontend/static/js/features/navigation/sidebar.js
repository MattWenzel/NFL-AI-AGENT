import { requestRender, state } from "../../core/state.js";

export function applySidebarView() {
  const view = state.sidebarView === "csvs" ? "csvs" : "chats";
  document.getElementById("pane-chats").hidden = view !== "chats";
  document.getElementById("pane-csvs").hidden = view !== "csvs";
  document.getElementById("tabChats").classList.toggle("active", view === "chats");
  document.getElementById("tabCsvs").classList.toggle("active", view === "csvs");
  const search = document.getElementById("sidebarSearch");
  search.placeholder = view === "csvs" ? "Search reports" : "Search conversations";
}

export function setSidebarView(view) {
  const next = view === "csvs" ? "csvs" : "chats";
  state.sidebarView = next;
  localStorage.setItem("nfl_sidebar_view", next);
  state.sidebarSearch = "";
  document.getElementById("sidebarSearch").value = "";
  requestRender();
}
