/* global pywebview */

/**
 * DJI Color Desk 的单页交互层。
 *
 * 页面只保留扫描、直接整理和报告导出。所有真实文件操作经由 pywebview
 * 调用本地 Python 服务；浏览器直接打开页面时仅展示未连接状态。
 */
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const modeClasses = { dlog: "dlog", dlog2: "dlog2", rec709: "rec709", rec2100_hlg: "hlg" };
  const modeLabels = {
    dlog: "D-Log",
    dlog2: "D-Log2",
    rec709: "普通 709",
    rec2100_hlg: "Rec.2100 HLG",
    unknown: "无法确认",
    error: "识别失败",
  };

  const state = {
    api: null,
    root: "",
    scanId: "",
    files: [],
    filter: "all",
    mode: "copy",
    activeTask: "",
    activeKind: "",
    submitting: false,
    needsRescan: false,
    toastTimer: 0,
  };

  function bridgeError(error) {
    if (error instanceof Error) return error.message;
    if (typeof error === "string") return error;
    if (error && typeof error.message === "string") return error.message;
    return "本地服务返回了未知错误";
  }

  function callApi(method, payload) {
    if (!state.api || typeof state.api[method] !== "function") {
      return Promise.reject(new Error("本地服务未连接，请使用 dji-color-web 启动工作台"));
    }
    try {
      return Promise.resolve(payload === undefined ? state.api[method]() : state.api[method](payload));
    } catch (error) {
      return Promise.reject(error);
    }
  }

  function showToast(title, copy, isError = false) {
    $("#toastTitle").textContent = title;
    $("#toastCopy").textContent = copy;
    $("#toast").classList.toggle("error", isError);
    $("#toast").classList.add("visible");
    window.clearTimeout(state.toastTimer);
    state.toastTimer = window.setTimeout(() => $("#toast").classList.remove("visible"), 4200);
  }

  function setConnection(connected, detail = "") {
    const status = $("#connectionStatus");
    status.classList.toggle("connected", connected);
    status.lastChild.textContent = connected ? "本地服务已连接" : (detail || "等待本地服务");
  }

  function isBusy() {
    return state.submitting || Boolean(state.activeTask);
  }

  function refreshControls() {
    const busy = isBusy();
    $("#chooseFolder").disabled = busy;
    $("#changeFolder").disabled = busy;
    $("#rescanFolder").disabled = busy || !state.root;
    $("#executeOrganize").disabled = busy || !state.scanId || state.needsRescan;
    $("#exportReport").disabled = busy || !state.scanId;
    $("#conflictPolicy").disabled = busy;
    $("#sidecarToggle").disabled = busy;
    $("#recursiveToggle").disabled = busy;
    $$(".mode-option").forEach((button) => { button.disabled = busy; });
    $("#cancelTask").hidden = !state.activeTask;
    $("#cancelTask").disabled = !state.activeTask;
    $("#executeOrganize").textContent = busy && state.activeKind === "organize"
      ? "正在整理…"
      : state.needsRescan ? "请重新识别后再整理" : "执行整理";
  }

  function countFor(mode) {
    if (mode === "all") return state.files.length;
    if (mode === "unknown") return state.files.filter((file) => file.status !== "ready").length;
    return state.files.filter((file) => file.mode === mode).length;
  }

  function updateSummary() {
    ["all", "dlog", "dlog2", "rec709", "rec2100_hlg", "unknown"].forEach((mode) => {
      const count = countFor(mode);
      $$("[data-filter]").filter((element) => element.dataset.filter === mode).forEach((element) => {
        const value = element.querySelector(".summary-value, span:last-child");
        if (value) value.textContent = String(count);
      });
    });
    const unprocessed = countFor("unknown");
    $("#resultsCopy").textContent = state.files.length
      ? `已识别 ${state.files.length} 个视频；${unprocessed} 个文件不会自动整理。`
      : "选择素材文件夹后，识别结果会显示在这里。";
  }

  function statusLabel(file) {
    return file.status_label || (file.status === "ready" ? "已识别" : "不自动整理");
  }

  function appendCell(row, value, title = "") {
    const cell = row.insertCell();
    cell.textContent = value || "—";
    if (title) cell.title = title;
    return cell;
  }

  function matchesFilter(file) {
    if (state.filter === "all") return true;
    if (state.filter === "unknown") return file.status !== "ready";
    return file.mode === state.filter;
  }

  function renderResults() {
    const keyword = $("#searchInput").value.trim().toLowerCase();
    const visible = state.files.filter((file) => {
      const searchText = `${file.name || ""} ${file.relative_path || ""}`.toLowerCase();
      return matchesFilter(file) && (!keyword || searchText.includes(keyword));
    });
    const body = $("#resultBody");
    body.replaceChildren();
    visible.forEach((file) => {
      const row = document.createElement("tr");
      const statusCell = row.insertCell();
      const status = document.createElement("span");
      status.className = `status ${file.status || "ready"}`;
      status.textContent = statusLabel(file);
      statusCell.appendChild(status);
      appendCell(row, file.name, file.path);
      const modeCell = row.insertCell();
      const dot = document.createElement("span");
      dot.className = `mode-dot ${modeClasses[file.mode] || ""}`;
      modeCell.append(dot, document.createTextNode(file.label || modeLabels[file.mode] || file.mode));
      appendCell(row, file.folder, file.path);
      appendCell(row, file.evidence, file.evidence_detail || "");
      body.appendChild(row);
    });
    if (!visible.length) {
      const row = document.createElement("tr");
      const cell = row.insertCell();
      cell.colSpan = 5;
      cell.textContent = state.files.length ? "没有匹配的文件" : "尚未扫描文件夹";
      body.appendChild(row);
    }
    $("#tableCount").textContent = `显示 ${visible.length} 个文件 · 共 ${state.files.length} 个文件`;
  }

  function setFilter(filter) {
    state.filter = filter;
    $$("[data-filter]").forEach((element) => element.classList.toggle("active", element.dataset.filter === filter));
    renderResults();
  }

  function renderOutcome(result, taskState) {
    const panel = $("#outcomePanel");
    panel.hidden = false;
    $("#successCount").textContent = String(result.success_count || 0);
    $("#skippedCount").textContent = String(result.skipped_count || 0);
    $("#failedCount").textContent = String(result.failed_count || 0);
    $("#outcomeCopy").textContent = taskState === "cancelled" || result.cancelled
      ? "任务已取消，以下是取消前已经发生的处理结果。"
      : "整理任务已结束。";

    const failures = (result.records || []).filter((record) => record.action !== "none" && !record.success);
    const list = $("#failureList");
    list.replaceChildren();
    failures.forEach((record) => {
      const item = document.createElement("li");
      item.textContent = `${record.source}：${record.message}`;
      list.appendChild(item);
    });
    list.hidden = !failures.length;
  }

  async function pollTask(taskId, onTerminal) {
    state.activeTask = taskId;
    while (true) {
      const task = await callApi("get_task_status", taskId);
      if (task.state === "queued" || task.state === "running") {
        $("#folderDetail").textContent = task.message || "正在处理…";
        await sleep(180);
        continue;
      }
      state.activeTask = "";
      state.activeKind = "";
      if (task.state === "failed") throw new Error(task.error || "任务执行失败");
      // 终态回调会写入 scanId 或 needsRescan；必须在回调之后刷新按钮状态。
      const result = onTerminal(task.result || {}, task.state);
      refreshControls();
      return result;
    }
  }

  async function startScan(root) {
    const selectedRoot = String(root || "").trim();
    if (!selectedRoot) return;
    state.root = selectedRoot;
    state.scanId = "";
    state.files = [];
    state.needsRescan = false;
    $("#outcomePanel").hidden = true;
    updateSummary();
    renderResults();
    state.activeKind = "scan";
    state.submitting = true;
    refreshControls();
    try {
      const handle = await callApi("start_scan", { directory: selectedRoot, recursive: $("#recursiveToggle").checked });
      state.submitting = false;
      await pollTask(handle.task_id, (result, taskState) => {
        if (taskState === "cancelled") {
          $("#folderDetail").textContent = "识别已取消";
          showToast("识别已取消", "未保留不完整的扫描结果。");
          return;
        }
        state.scanId = result.scan_id;
        state.files = result.results || [];
        $("#folderPath").textContent = result.root || selectedRoot;
        $("#folderDetail").textContent = `${result.recursive ? "包含子文件夹" : "仅当前文件夹"} · 识别完成`;
        updateSummary();
        renderResults();
        showToast("识别完成", `发现 ${state.files.length} 个视频。`);
      });
    } catch (error) {
      state.activeTask = "";
      state.activeKind = "";
      state.submitting = false;
      refreshControls();
      showToast("识别失败", bridgeError(error), true);
    }
  }

  async function chooseFolder() {
    try {
      const selected = await callApi("choose_directory");
      if (selected) await startScan(selected);
    } catch (error) {
      showToast("无法选择文件夹", bridgeError(error), true);
    }
  }

  async function executeOrganize() {
    if (!state.scanId) {
      showToast("尚未完成识别", "请先选择素材文件夹并等待识别完成。", true);
      return;
    }
    state.activeKind = "organize";
    state.submitting = true;
    refreshControls();
    try {
      const handle = await callApi("execute_organize", {
        scan_id: state.scanId,
        mode: state.mode,
        conflict_policy: $("#conflictPolicy").value,
        with_sidecars: $("#sidecarToggle").checked,
      });
      state.submitting = false;
      await pollTask(handle.task_id, (result, taskState) => {
        renderOutcome(result, taskState);
        // 文件系统已经发生变化，必须重新扫描后才能再次整理，避免复用陈旧结果。
        state.needsRescan = true;
        const title = taskState === "cancelled" ? "整理已取消" : result.failed_count ? "整理完成，但有失败项" : "整理完成";
        const copy = `成功 ${result.success_count || 0} 个，跳过 ${result.skipped_count || 0} 个，失败 ${result.failed_count || 0} 个。`;
        showToast(title, copy, Boolean(result.failed_count));
        $("#folderDetail").textContent = taskState === "cancelled" ? "整理已取消，已显示部分结果" : "整理已完成，已显示处理结果";
      });
    } catch (error) {
      state.activeTask = "";
      state.activeKind = "";
      state.submitting = false;
      refreshControls();
      showToast("整理失败", bridgeError(error), true);
    }
  }

  async function cancelActiveTask() {
    if (!state.activeTask) return;
    try {
      await callApi("cancel_task", state.activeTask);
      showToast("已请求取消", "当前文件完成后将停止后续处理。");
    } catch (error) {
      showToast("取消任务失败", bridgeError(error), true);
    }
  }

  async function exportReport() {
    if (!state.scanId) return;
    const format = window.confirm("确定导出 JSON 报告吗？点击“取消”将导出 CSV 报告。") ? "json" : "csv";
    try {
      const path = await callApi("choose_report_path", format);
      if (!path) return;
      const result = await callApi("export_report", { scan_id: state.scanId, output: path, format });
      showToast("报告已导出", `${result.count} 条识别结果已写入 ${result.path}`);
    } catch (error) {
      showToast("报告导出失败", bridgeError(error), true);
    }
  }

  function handleDroppedDirectory(path) {
    const selectedRoot = String(path || "").trim();
    if (!selectedRoot) {
      showToast("无法读取拖入目录", "没有收到本地完整路径，请点击“选择素材文件夹”。", true);
      return;
    }
    void startScan(selectedRoot);
  }

  // Python 侧通过 window.evaluate_js 调用此入口，桌面拖拽复用扫描流程。
  window.djiColorDeskHandleDrop = handleDroppedDirectory;

  function bindEvents() {
    $$("[data-filter]").forEach((element) => element.addEventListener("click", () => setFilter(element.dataset.filter)));
    $("#searchInput").addEventListener("input", renderResults);
    $("#chooseFolder").addEventListener("click", chooseFolder);
    $("#changeFolder").addEventListener("click", chooseFolder);
    $("#rescanFolder").addEventListener("click", () => startScan(state.root));
    $("#cancelTask").addEventListener("click", cancelActiveTask);
    $("#executeOrganize").addEventListener("click", executeOrganize);
    $("#exportReport").addEventListener("click", exportReport);
    $$(".mode-option").forEach((button) => button.addEventListener("click", () => {
      state.mode = button.dataset.mode;
      $$(".mode-option").forEach((option) => option.classList.toggle("selected", option === button));
    }));
    $("#recursiveToggle").addEventListener("change", () => {
      if (state.root && !isBusy()) void startScan(state.root);
    });
    document.addEventListener("dragover", (event) => event.preventDefault());
    document.addEventListener("drop", (event) => {
      event.preventDefault();
      const dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      // 少数 pywebview 渲染器也会把完整路径同步到 JavaScript；优先直接使用，
      // 其余渲染器仍由 Python DOM 事件通过 djiColorDeskHandleDrop 转发。
      const path = dropped && (dropped.pywebviewFullPath || dropped.path);
      if (path) handleDroppedDirectory(path);
      else if (!state.api) showToast("无法读取拖入目录", "普通浏览器无法提供本地目录路径，请使用 dji-color-web。", true);
    });
  }

  async function connectBridge() {
    state.api = window.pywebview && window.pywebview.api ? window.pywebview.api : null;
    if (!state.api) {
      setConnection(false);
      return;
    }
    try {
      const serviceState = await callApi("get_state");
      setConnection(Boolean(serviceState.connected), "Python 核心未响应");
    } catch (error) {
      setConnection(false, bridgeError(error));
    }
  }

  bindEvents();
  updateSummary();
  renderResults();
  refreshControls();
  window.addEventListener("pywebviewready", connectBridge);
  void connectBridge();
})();
