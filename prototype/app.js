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
  const PAGE_SIZE = 100;
  const SEARCH_DELAY = 180;
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
    filteredFiles: [],
    page: 1,
    filter: "all",
    mode: "copy",
    activeTask: "",
    activeKind: "",
    submitting: false,
    needsRescan: false,
    cancelRequested: false,
    searchTimer: 0,
    toastTimer: 0,
    dragDepth: 0,
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
    const hasActionableFiles = state.files.some((file) => file.status === "ready");
    $("#chooseFolder").disabled = busy;
    $("#changeFolder").disabled = busy;
    $("#rescanFolder").disabled = busy || !state.root;
    $("#executeOrganize").disabled = busy || !state.scanId || state.needsRescan || !hasActionableFiles;
    $("#exportReport").disabled = busy || !state.scanId;
    $("#conflictPolicy").disabled = busy;
    $("#sidecarToggle").disabled = busy;
    $("#recursiveToggle").disabled = busy;
    $$(".mode-option").forEach((button) => { button.disabled = busy; });
    $("#cancelTask").hidden = !state.activeTask;
    $("#cancelTask").disabled = !state.activeTask || state.cancelRequested;
    $("#cancelTask").textContent = state.cancelRequested ? "正在取消…" : "取消任务";
    $("#taskProgress").hidden = !busy;
    $("#executeOrganize").textContent = busy && state.activeKind === "organize"
      ? "正在整理…"
      : state.needsRescan ? "请重新识别后再整理" : "执行整理";
  }

  /** 有总数时显示实际进度；目录枚举等未知总数阶段保留等待动画。 */
  function updateTaskProgress(task = {}) {
    const total = Number.isFinite(task.total) ? Math.max(0, task.total) : 0;
    const completed = Number.isFinite(task.completed) ? Math.min(total, Math.max(0, task.completed)) : 0;
    const progress = $("#taskProgress");
    progress.classList.toggle("determinate", total > 0);
    $("#taskProgressValue").style.width = total ? `${completed / total * 100}%` : "";
    if (total) {
      progress.setAttribute("aria-valuemax", String(total));
      progress.setAttribute("aria-valuenow", String(completed));
    } else {
      progress.removeAttribute("aria-valuemax");
      progress.removeAttribute("aria-valuenow");
    }
    const message = state.cancelRequested ? "正在取消，等待当前文件完成" : task.message;
    if (message) $("#folderDetail").textContent = total ? `${message} · ${completed} / ${total}` : message;
  }

  /**
   * 在窄窗口中打开或关闭整理抽屉；宽窗口仍由 CSS 保持双栏布局。
   */
  function setOrganizerOpen(open) {
    const panel = $("#organizePanel");
    panel.classList.toggle("open", open);
    $("#toggleOrganizer").setAttribute("aria-expanded", String(open));
    refreshBackdrop();
  }

  /** 根据当前浮层统一维护遮罩，避免多个入口各自留下不可点击的遮罩。 */
  function refreshBackdrop() {
    const visible = $("#organizePanel").classList.contains("open") || !$("#outcomePanel").hidden;
    $("#organizerBackdrop").classList.toggle("visible", visible);
  }

  function closeOutcome() {
    $("#outcomePanel").hidden = true;
    refreshBackdrop();
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
        const value = element.querySelector(".summary-value");
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

  /** 仅在搜索、筛选或扫描结果变化时遍历数据，翻页复用筛选结果。 */
  function filterResults() {
    window.clearTimeout(state.searchTimer);
    const keyword = $("#searchInput").value.trim().toLowerCase();
    state.filteredFiles = state.files.filter((file) => matchesFilter(file) && (!keyword || file.searchText.includes(keyword)));
    state.page = 1;
    renderResults();
  }

  function scheduleSearch() {
    window.clearTimeout(state.searchTimer);
    state.searchTimer = window.setTimeout(filterResults, SEARCH_DELAY);
  }

  /** 一页最多创建固定数量的行，避免数万条结果阻塞桌面界面。 */
  function renderResults() {
    const total = state.filteredFiles.length;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    state.page = Math.min(pages, Math.max(1, state.page));
    const start = (state.page - 1) * PAGE_SIZE;
    const visible = state.filteredFiles.slice(start, start + PAGE_SIZE);
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
    $("#tableCount").textContent = total
      ? `显示 ${start + 1}–${start + visible.length} 项 · 匹配 ${total} 项 · 共 ${state.files.length} 项`
      : `匹配 0 项 · 共 ${state.files.length} 项`;
    $("#pageNumber").textContent = `${state.page} / ${pages}`;
    $("#previousPage").disabled = state.page <= 1;
    $("#nextPage").disabled = state.page >= pages;
    $("#resultTableWrap").scrollTop = 0;
  }

  function setFilter(filter) {
    state.filter = filter;
    $$("[data-filter]").forEach((element) => {
      const selected = element.dataset.filter === filter;
      element.classList.toggle("active", selected);
      element.setAttribute("aria-pressed", String(selected));
    });
    filterResults();
  }

  function renderOutcome(result, taskState) {
    const panel = $("#outcomePanel");
    panel.hidden = false;
    setOrganizerOpen(false);
    refreshBackdrop();
    $("#successCount").textContent = String(result.success_count || 0);
    $("#skippedCount").textContent = String(result.skipped_count || 0);
    $("#failedCount").textContent = String(result.failed_count || 0);
    $("#pendingCount").textContent = String(result.pending_count || 0);
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
    // 句柄到达后立即显示取消入口，不能等到终态才刷新控件。
    refreshControls();
    let pollFailures = 0;
    while (true) {
      let task;
      try {
        task = await callApi("get_task_status", taskId);
        if (!["queued", "running", "completed", "cancelled", "failed"].includes(task.state)) {
          throw new Error("本地服务返回了未知任务状态");
        }
        if (pollFailures) setConnection(true);
        pollFailures = 0;
      } catch (error) {
        // 通信失败不代表任务结束：保留任务句柄和操作锁，恢复连接后继续收敛终态。
        pollFailures += 1;
        setConnection(false, "任务状态连接中断");
        $("#folderDetail").textContent = "暂时无法读取任务状态，正在重试…";
        if (pollFailures === 1) showToast("正在恢复任务状态", bridgeError(error), true);
        await sleep(Math.min(3000, pollFailures * 1000));
        continue;
      }
      updateTaskProgress(task);
      if (task.state === "queued" || task.state === "running") {
        await sleep(180);
        continue;
      }
      state.activeTask = "";
      state.activeKind = "";
      state.cancelRequested = false;
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
    // pywebview 可能同时派发 JavaScript 与 Python 两路 drop 事件；繁忙检查可避免
    // 同一个目录被重复提交，也禁止整理期间启动会读取变化中文件的扫描。
    if (isBusy()) return;
    state.root = selectedRoot;
    state.scanId = "";
    state.files = [];
    state.filter = "all";
    state.needsRescan = false;
    state.cancelRequested = false;
    $("#searchInput").value = "";
    $("#folderPath").textContent = selectedRoot;
    $("#folderPath").title = selectedRoot;
    $("#folderDetail").textContent = "正在准备识别…";
    closeOutcome();
    updateSummary();
    setFilter("all");
    state.activeKind = "scan";
    state.submitting = true;
    updateTaskProgress();
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
        state.files = (result.results || []).map((file) => ({
          ...file,
          searchText: `${file.name || ""} ${file.relative_path || ""}`.toLowerCase(),
        }));
        $("#folderPath").textContent = result.root || selectedRoot;
        $("#folderPath").title = result.root || selectedRoot;
        $("#folderDetail").textContent = `${result.recursive ? "包含子文件夹" : "仅当前文件夹"} · 识别完成`;
        updateSummary();
        filterResults();
        showToast("识别完成", `发现 ${state.files.length} 个视频。`);
      });
    } catch (error) {
      state.activeTask = "";
      state.activeKind = "";
      state.submitting = false;
      $("#folderDetail").textContent = "识别失败，请重新识别";
      refreshControls();
      showToast("识别失败", bridgeError(error), true);
    }
  }

  async function chooseFolder() {
    if (isBusy()) return;
    // 系统目录对话框打开期间也要锁定入口，避免快速点击弹出多个对话框。
    state.submitting = true;
    refreshControls();
    try {
      const selected = await callApi("choose_directory");
      state.submitting = false;
      refreshControls();
      if (selected) await startScan(selected);
    } catch (error) {
      state.submitting = false;
      refreshControls();
      showToast("无法选择文件夹", bridgeError(error), true);
    }
  }

  async function executeOrganize() {
    if (isBusy()) return;
    if (state.needsRescan) {
      showToast("需要重新识别", "上次整理已提交，请重新识别后再整理。", true);
      return;
    }
    if (!state.scanId) {
      showToast("尚未完成识别", "请先选择素材文件夹并等待识别完成。", true);
      return;
    }
    state.activeKind = "organize";
    state.submitting = true;
    state.cancelRequested = false;
    // 即使桥接丢失提交响应，后台也可能已经修改文件，不能继续使用本次扫描。
    state.needsRescan = true;
    updateTaskProgress({ message: "正在提交整理任务…" });
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
        const copy = `成功 ${result.success_count || 0} 个，跳过 ${result.skipped_count || 0} 个，失败 ${result.failed_count || 0} 个，未执行 ${result.pending_count || 0} 个。`;
        showToast(title, copy, Boolean(result.failed_count));
        $("#folderDetail").textContent = taskState === "cancelled" ? "整理已取消，已显示部分结果" : "整理已完成，已显示处理结果";
      });
    } catch (error) {
      state.activeTask = "";
      state.activeKind = "";
      state.submitting = false;
      $("#folderDetail").textContent = "整理失败，请重新识别后再整理";
      refreshControls();
      showToast("整理失败", bridgeError(error), true);
    }
  }

  async function cancelActiveTask() {
    if (!state.activeTask || state.cancelRequested) return;
    const taskId = state.activeTask;
    state.cancelRequested = true;
    refreshControls();
    try {
      await callApi("cancel_task", taskId);
      if (state.activeTask !== taskId) return;
      showToast("已请求取消", "当前文件完成后将停止后续处理。");
    } catch (error) {
      if (state.activeTask !== taskId) return;
      state.cancelRequested = false;
      refreshControls();
      showToast("取消任务失败", bridgeError(error), true);
    }
  }

  async function exportReport(format) {
    if (!state.scanId || isBusy()) return;
    // 保存对话框和报告写入期间保持单任务状态，避免扫描结果同时被替换。
    state.submitting = true;
    refreshControls();
    try {
      const path = await callApi("choose_report_path", format);
      if (!path) return;
      const result = await callApi("export_report", { scan_id: state.scanId, output: path, format });
      showToast("报告已导出", `${result.count} 条识别结果已写入 ${result.path}`);
    } catch (error) {
      showToast("报告导出失败", bridgeError(error), true);
    } finally {
      state.submitting = false;
      refreshControls();
    }
  }

  function openExportDialog() {
    if (!state.scanId || isBusy()) return;
    $("#exportDialog").showModal();
  }

  /** 桌面快捷键只覆盖高频、无破坏性的入口。 */
  function handleShortcut(event) {
    const modifier = event.ctrlKey || event.metaKey;
    if (modifier && event.key.toLowerCase() === "o") {
      event.preventDefault();
      void chooseFolder();
      return;
    }
    if (modifier && event.key.toLowerCase() === "f") {
      event.preventDefault();
      $("#searchInput").focus();
      $("#searchInput").select();
      return;
    }
    if (event.key === "F5" || (modifier && event.key.toLowerCase() === "r")) {
      // 页面重载会丢失后台任务句柄；统一解释为闲置时重新识别。
      event.preventDefault();
      if (state.root && !isBusy()) void startScan(state.root);
      return;
    }
    if (event.key === "Escape") {
      setOrganizerOpen(false);
      closeOutcome();
    }
  }

  function handleDroppedDirectory(path) {
    const selectedRoot = String(path || "").trim();
    if (!selectedRoot) {
      showToast("无法读取拖入目录", "没有收到本地完整路径，请点击“选择素材文件夹”。", true);
      return;
    }
    if (isBusy()) return;
    void startScan(selectedRoot);
  }

  // Python 侧通过 window.evaluate_js 调用此入口，桌面拖拽复用扫描流程。
  window.djiColorDeskHandleDrop = handleDroppedDirectory;

  function bindEvents() {
    $$("[data-filter]").forEach((element) => element.addEventListener("click", () => setFilter(element.dataset.filter)));
    $("#searchInput").addEventListener("input", scheduleSearch);
    $("#previousPage").addEventListener("click", () => { state.page -= 1; renderResults(); });
    $("#nextPage").addEventListener("click", () => { state.page += 1; renderResults(); });
    $("#chooseFolder").addEventListener("click", chooseFolder);
    $("#changeFolder").addEventListener("click", chooseFolder);
    $("#rescanFolder").addEventListener("click", () => startScan(state.root));
    $("#cancelTask").addEventListener("click", cancelActiveTask);
    $("#executeOrganize").addEventListener("click", executeOrganize);
    $("#exportReport").addEventListener("click", openExportDialog);
    $("#cancelExport").addEventListener("click", () => $("#exportDialog").close());
    $("#confirmExport").addEventListener("click", () => {
      const selected = document.querySelector('input[name="exportFormat"]:checked');
      $("#exportDialog").close();
      void exportReport(selected ? selected.value : "csv");
    });
    $("#toggleOrganizer").addEventListener("click", () => setOrganizerOpen(true));
    $("#closeOrganizer").addEventListener("click", () => setOrganizerOpen(false));
    $("#closeOutcome").addEventListener("click", closeOutcome);
    $("#organizerBackdrop").addEventListener("click", () => {
      setOrganizerOpen(false);
      closeOutcome();
    });
    $$(".mode-option").forEach((button) => button.addEventListener("click", () => {
      state.mode = button.dataset.mode;
      $$(".mode-option").forEach((option) => {
        const selected = option === button;
        option.classList.toggle("selected", selected);
        option.setAttribute("aria-pressed", String(selected));
      });
    }));
    $("#recursiveToggle").addEventListener("change", () => {
      if (state.root && !isBusy()) void startScan(state.root);
    });
    document.addEventListener("keydown", handleShortcut);
    document.addEventListener("dragenter", (event) => {
      const types = Array.from((event.dataTransfer && event.dataTransfer.types) || []);
      if (!types.includes("Files")) return;
      event.preventDefault();
      state.dragDepth += 1;
      $("#dragOverlay").classList.add("visible");
    });
    document.addEventListener("dragover", (event) => event.preventDefault());
    document.addEventListener("dragleave", () => {
      state.dragDepth = Math.max(0, state.dragDepth - 1);
      if (!state.dragDepth) $("#dragOverlay").classList.remove("visible");
    });
    document.addEventListener("drop", (event) => {
      event.preventDefault();
      state.dragDepth = 0;
      $("#dragOverlay").classList.remove("visible");
      const dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      // 少数 pywebview 渲染器也会把完整路径同步到 JavaScript；优先直接使用，
      // 其余渲染器仍由 Python DOM 事件通过 djiColorDeskHandleDrop 转发。
      const path = dropped && (dropped.pywebviewFullPath || dropped.path);
      if (path) handleDroppedDirectory(path);
      else if (!state.api) showToast("无法读取拖入目录", "普通浏览器无法提供本地目录路径，请使用 dji-color-web。", true);
    });
    window.addEventListener("resize", () => {
      // 从抽屉断点恢复到双栏时立即清理遮罩，避免它挡住宽窗口工作区。
      if (window.innerWidth > 1040) setOrganizerOpen(false);
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
  setFilter(state.filter);
  $$(".mode-option").forEach((option) => {
    option.setAttribute("aria-pressed", String(option.dataset.mode === state.mode));
  });
  refreshControls();
  window.addEventListener("pywebviewready", connectBridge);
  void connectBridge();
})();
