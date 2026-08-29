/* global pywebview */

/**
 * DJI Color Desk 的生产交互层。
 *
 * 页面保留原型中的视觉结构，但不再内置演示数据：所有扫描、预演、执行、
 * 导出和撤销动作都通过 pywebview 的 Python bridge 完成。页面直接打开时，
 * 会显示“等待本地服务”，方便设计验收；桌面入口加载后会自动连接服务。
 */
(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const sleep = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
  const modeLabels = {
    dlog: "D-Log",
    dlog2: "D-Log2",
    rec709: "普通709",
    rec2100_hlg: "Rec.2100 HLG（HDR）",
    unknown: "无法确认",
    error: "识别失败",
  };
  const modeClasses = {
    dlog: "dlog",
    dlog2: "dlog2",
    rec709: "rec709",
    rec2100_hlg: "hlg",
    unknown: "unknown",
    error: "unknown",
  };
  const operationText = { copy: "复制", move: "移动", prefix: "添加前缀", undo: "撤销" };
  const operationConfig = {
    copy: {
      action: "复制",
      safety: "不会修改原文件",
      target: "dlog / dlog2 / rec709 / hlg",
      fileLabel: "待复制文件",
      spaceLabel: "预计新增空间",
      modalTitle: "确认复制这些素材？",
      modalCopy: "将复制可识别的视频到分类文件夹，原始视频保留不变。待确认文件不会参与本次整理，完成后会生成可撤销的 manifest。",
    },
    move: {
      action: "移动",
      safety: "会改变文件位置",
      target: "dlog / dlog2 / rec709 / hlg",
      fileLabel: "待移动文件",
      spaceLabel: "空间占用变化",
      modalTitle: "确认移动这些素材？",
      modalCopy: "将把可识别的视频移动到分类文件夹，原始路径会发生变化。待确认文件不会参与本次整理，完成后会生成可撤销的 manifest。",
    },
    prefix: {
      action: "添加前缀",
      safety: "只改文件名",
      target: "dlog_ / dlog2_ / hlg_ 前缀",
      fileLabel: "待改名文件",
      spaceLabel: "空间占用变化",
      modalTitle: "确认添加文件名前缀？",
      modalCopy: "将为 D-Log、D-Log2 与 HLG HDR 视频添加文件名前缀，不会移动视频，也不会新增占用空间。其他文件保持不变。",
    },
  };

  const state = {
    api: null,
    root: "",
    scanId: "",
    files: [],
    plan: null,
    filter: "all",
    mode: "copy",
    recursive: true,
    sidecar: true,
    conflictPolicy: "suffix",
    pendingAction: "plan",
    pendingManifest: "",
    activeTask: "",
    executed: false,
    logs: [],
    toastTimer: 0,
  };

  function addLog(message) {
    state.logs.push(`${new Date().toLocaleTimeString()} · ${message}`);
    state.logs = state.logs.slice(-8);
  }

  function setConnection(connected, detail) {
    const title = $(".connection-head");
    const copy = $(".connection-copy");
    if (!title || !copy) return;
    title.innerHTML = `<span class="connection-dot"></span>${connected ? "本地服务已连接" : "等待本地服务"}`;
    const currentDot = title.querySelector(".connection-dot");
    if (currentDot) currentDot.style.background = connected ? "#ef938b" : "#9e7975";
    copy.replaceChildren(
      document.createTextNode(connected ? "Python 核心就绪" : (detail || "请通过 dji-color-web 启动工作台")),
      document.createElement("br"),
      document.createTextNode(connected ? "Bridge · 安全本地模式" : "仅本地页面预览"),
    );
  }

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
      const result = payload === undefined ? state.api[method]() : state.api[method](payload);
      return Promise.resolve(result);
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
    addLog(`${title}：${copy}`);
    if (isError) console.error(`[DJI Color Desk] ${title}：${copy}`);
    else console.info(`[DJI Color Desk] ${title}：${copy}`);
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 B";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let value = Number(bytes);
    let index = 0;
    while (value >= 1024 && index < units.length - 1) {
      value /= 1024;
      index += 1;
    }
    return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
  }

  function setBusy(busy, message = "") {
    $$('button, input, select').forEach((element) => {
      if (element.id === "cancelModal" || element.id === "modalBackdrop") return;
      element.disabled = busy;
    });
    const cancelButton = $("#cancelTask");
    if (cancelButton) {
      cancelButton.hidden = !busy;
      cancelButton.disabled = !busy;
    }
    if (busy) {
      $("#folderDetail").textContent = message || "正在处理…";
      $("#executePlan").textContent = "正在处理…";
    }
  }

  function summaryCount(mode) {
    return state.files.filter((file) => mode === "all" || file.mode === mode).length;
  }

  function statusLabel(file) {
    return file.status_label || (file.status === "review" ? "待确认" : file.status === "error" ? "识别失败" : "已识别");
  }

  function updateSummary() {
    const modes = ["all", "dlog", "dlog2", "rec709", "rec2100_hlg", "unknown"];
    modes.forEach((mode) => {
      const card = $(`.summary-card[data-filter="${mode}"]`);
      if (card) card.querySelector(".summary-value").textContent = String(summaryCount(mode));
      $$('.filter-pill').filter((element) => element.dataset.filter === mode).forEach((element) => {
        const count = element.querySelector("span");
        if (count) count.textContent = String(summaryCount(mode));
      });
    });
    const total = state.files.length;
    const review = state.files.filter((file) => file.status === "review" || file.status === "error").length;
    $("#resultsTitle").nextElementSibling.textContent = total
      ? `${total} 个视频已完成快速识别，${review} 个文件需要人工确认。`
      : "选择素材文件夹后，识别结果会显示在这里。";
    updatePlanMode();
  }

  function appendTextCell(row, text, title = "") {
    const cell = document.createElement("td");
    cell.textContent = text || "—";
    if (title) cell.title = title;
    row.appendChild(cell);
    return cell;
  }

  function renderResults() {
    const keyword = $("#searchInput").value.trim().toLowerCase();
    const visible = state.files.filter((file) => {
      const filterMatches = state.filter === "all" || file.mode === state.filter;
      const searchMatches = !keyword || file.name.toLowerCase().includes(keyword) || file.relative_path.toLowerCase().includes(keyword);
      return filterMatches && searchMatches;
    });
    const body = $("#resultBody");
    body.replaceChildren();
    visible.forEach((file) => {
      const row = document.createElement("tr");
      const status = document.createElement("span");
      status.className = `status ${file.status || "ready"}`;
      status.textContent = statusLabel(file);
      const statusCell = row.insertCell();
      statusCell.appendChild(status);
      appendTextCell(row, file.name, file.path);
      const modeCell = row.insertCell();
      const dot = document.createElement("span");
      dot.className = `mode-dot ${modeClasses[file.mode] || "unknown"}`;
      modeCell.appendChild(dot);
      modeCell.appendChild(document.createTextNode(file.label || modeLabels[file.mode] || file.mode));
      appendTextCell(row, file.folder, file.path);
      appendTextCell(row, file.evidence, file.evidence_detail || "");
      body.appendChild(row);
    });
    if (!visible.length) {
      const row = document.createElement("tr");
      const cell = row.insertCell();
      cell.colSpan = 5;
      cell.textContent = state.files.length ? "没有匹配的文件" : "尚未扫描文件夹";
      cell.style.cssText = "padding: 28px 12px; color: var(--muted); text-align: center;";
      body.appendChild(row);
    }
    const resultLabel = keyword ? `匹配 ${visible.length} 个文件` : `共 ${state.files.length} 个文件`;
    $("#tableCount").innerHTML = `显示 ${visible.length} 个文件 · ${resultLabel} · <strong>向下滚动浏览明细</strong>`;
  }

  function setFilter(filter) {
    state.filter = filter;
    $$('.filter-pill, .summary-card').forEach((element) => element.classList.toggle("active", element.dataset.filter === filter));
    $$('.summary-card').forEach((element) => element.classList.toggle("selected", element.dataset.filter === filter));
    renderResults();
  }

  function planCount(mode) {
    if (!state.plan) return 0;
    return state.plan.items.filter((item) => !item.skipped && item.action !== "none" && item.mode === mode).length;
  }

  function updatePlanMode() {
    const config = operationConfig[state.mode];
    const count = state.plan ? state.plan.actionable_count : 0;
    const skipped = state.plan ? state.plan.skipped_count : state.files.length;
    $("#safePill").textContent = config.safety;
    $("#planKicker").textContent = state.mode === "prefix" ? "下一步 · 改名预演" : "下一步 · 整理预演";
    $("#planTitle").textContent = `准备好${config.action} ${count} 个文件`;
    $("#planCopy").textContent = skipped ? `待确认或无需处理的 ${skipped} 个文件会自动跳过，稍后可从明细中单独处理。` : "所有识别结果都已生成整理目标。";
    $("#targetValue").textContent = config.target;
    $("#previewValue").textContent = state.plan ? `${count} 个文件 · ${state.mode === "copy" ? formatBytes(state.plan.estimated_bytes) : "不新增占用空间"}` : "等待生成计划";
    $("#executePlan").textContent = `执行${config.action} · ${count} 个文件`;
    $("#executePlan").disabled = !state.plan || count === 0 || state.executed;
    $$('.mode-option').forEach((element) => {
      element.classList.toggle("selected", element.dataset.mode === state.mode);
      const countLabel = element.querySelector(".mode-option-count");
      if (countLabel) countLabel.textContent = String(planCount(element.dataset.mode));
    });
    if (state.executed) $("#executePlan").textContent = "✓ 整理已完成 · 请重新识别";
  }

  async function pollTask(taskId, onCompleted) {
    state.activeTask = taskId;
    while (true) {
      const task = await callApi("get_task_status", taskId);
      if (task.state === "running" || task.state === "queued") {
        setBusy(true, task.message || "正在处理…");
        await sleep(140);
        continue;
      }
      if (task.state === "failed") throw new Error(task.error || "任务执行失败");
      if (task.state === "cancelled") {
        state.activeTask = "";
        setBusy(false);
        showToast("任务已取消", task.message || "未继续处理后续文件");
        return null;
      }
      setBusy(false);
      state.activeTask = "";
      return onCompleted(task.result || {});
    }
  }

  async function cancelActiveTask() {
    if (!state.activeTask) return;
    try {
      await callApi("cancel_task", state.activeTask);
      showToast("已请求取消", "当前文件处理完成后将停止后续任务。");
    } catch (error) {
      showToast("取消任务失败", bridgeError(error), true);
    }
  }

  async function startScan(root) {
    const selectedRoot = String(root || "").trim();
    if (!selectedRoot) return;
    state.root = selectedRoot;
    state.scanId = "";
    state.files = [];
    state.plan = null;
    state.executed = false;
    updateSummary();
    renderResults();
    setBusy(true, "正在提交扫描任务…");
    try {
      const handle = await callApi("start_scan", { directory: selectedRoot, recursive: state.recursive });
      addLog(`已提交扫描任务 ${handle.task_id}`);
      await pollTask(handle.task_id, (result) => {
        state.scanId = result.scan_id;
        state.files = result.results || [];
        $("#folderPath").textContent = result.root;
        $("#folderDetail").textContent = `${result.recursive ? "包含子文件夹" : "仅当前文件夹"} · 刚刚识别完成`;
        updateSummary();
        renderResults();
        showToast("识别完成", `发现 ${state.files.length} 个视频，其中 ${state.files.filter((file) => file.status !== "ready").length} 个需要人工确认。`);
        return result;
      });
      if (state.scanId) await rebuildPlan();
    } catch (error) {
      state.activeTask = "";
      setBusy(false);
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

  async function rebuildPlan() {
    if (!state.scanId) {
      updatePlanMode();
      return;
    }
    setBusy(true, "正在生成整理预演…");
    try {
      state.plan = await callApi("build_plan", {
        scan_id: state.scanId,
        mode: state.mode,
        conflict_policy: state.conflictPolicy,
        name_template: state.mode === "prefix" ? "" : null,
        // 留空目录模板以使用核心内置映射，确保 HLG 使用 hlg/ 而不是 rec2100_hlg/。
        dir_template: null,
        with_sidecars: state.sidecar,
      });
      setBusy(false);
      updatePlanMode();
    } catch (error) {
      state.activeTask = "";
      setBusy(false);
      state.plan = null;
      updatePlanMode();
      showToast("无法生成整理计划", bridgeError(error), true);
    }
  }

  function openConfirm() {
    if (!state.plan || state.plan.actionable_count === 0) {
      showToast("暂无可执行文件", "请先选择素材文件夹，或检查当前整理方式。", true);
      return;
    }
    state.pendingAction = "plan";
    const config = operationConfig[state.mode];
    $("#modalTitle").textContent = config.modalTitle;
    $("#modalCopy").textContent = config.modalCopy;
    $("#modalFiles").textContent = String(state.plan.actionable_count);
    $("#modalFileLabel").textContent = config.fileLabel;
    $("#modalSpace").textContent = state.mode === "copy" ? formatBytes(state.plan.estimated_bytes) : "不新增";
    $("#modalSpaceLabel").textContent = config.spaceLabel;
    $("#modalSkipped").textContent = String(state.plan.skipped_count);
    $("#modalSkippedLabel").textContent = "自动跳过";
    $("#confirmExecute").textContent = "确认执行";
    $("#confirmModal").classList.add("visible");
    $("#modalBackdrop").classList.add("visible");
  }

  function openUndoConfirm(data) {
    state.pendingAction = "undo";
    state.pendingManifest = data.manifest_path;
    const undo = data.undo || {};
    $("#modalTitle").textContent = "确认撤销这次整理？";
    $("#modalCopy").textContent = "程序会根据 manifest 将移动/改名文件恢复到原路径，复制出的文件只有在大小仍匹配时才会删除。";
    $("#modalFiles").textContent = String(undo.actionable_count || 0);
    $("#modalFileLabel").textContent = "待撤销文件";
    $("#modalSpace").textContent = "按记录校验";
    $("#modalSpaceLabel").textContent = "安全策略";
    $("#modalSkipped").textContent = String(undo.skipped_count || 0);
    $("#modalSkippedLabel").textContent = "无法撤销";
    $("#confirmExecute").textContent = "确认撤销";
    $("#confirmModal").classList.add("visible");
    $("#modalBackdrop").classList.add("visible");
  }

  function closeConfirm() {
    $("#confirmModal").classList.remove("visible");
    $("#modalBackdrop").classList.remove("visible");
    state.pendingAction = "plan";
  }

  async function confirmPendingAction() {
    const action = state.pendingAction;
    closeConfirm();
    setBusy(true, action === "undo" ? "正在撤销整理…" : "正在执行整理…");
    try {
      const handle = action === "undo"
        ? await callApi("execute_undo", { manifest_path: state.pendingManifest, confirmed: true, on_missing: "error" })
        : await callApi("execute_plan", { plan_id: state.plan.plan_id, confirmed: true });
      await pollTask(handle.task_id, (result) => {
        if (action === "undo") {
          showToast("撤销完成", `已处理 ${result.success_count || 0} 个文件，撤销记录已保存。`);
        } else {
          state.executed = true;
          state.pendingManifest = result.manifest_path || "";
          $("#folderDetail").textContent = `刚刚完成${operationText[state.mode]} · manifest 已保存`;
          $("#activityTitle").textContent = `最近一次操作记录 · ${new Date().toLocaleString()}`;
          $("#activitySubtitle").textContent = `${operationText[state.mode]} ${result.success_count || 0} 个文件 · manifest 已保存`;
          const skippedText = result.skipped_count ? `，跳过 ${result.skipped_count} 个` : "";
          showToast("整理完成，记录已保存", `${result.success_count || 0} 个文件已${operationText[state.mode]}${skippedText}，manifest 可用于撤销本次操作。`);
        }
        updatePlanMode();
        return result;
      });
    } catch (error) {
      state.activeTask = "";
      setBusy(false);
      showToast(action === "undo" ? "撤销失败" : "整理失败", bridgeError(error), true);
    }
  }

  async function exportReport() {
    if (!state.scanId) {
      showToast("暂无识别报告", "请先完成一次目录识别。", true);
      return;
    }
    // 确认导出 JSON，取消则导出更适合表格软件打开的 CSV。
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

  async function loadManifest(path) {
    try {
      const data = await callApi("load_manifest", { manifest_path: path });
      openUndoConfirm(data);
    } catch (error) {
      showToast("载入记录失败", bridgeError(error), true);
    }
  }

  async function chooseManifest() {
    try {
      const path = await callApi("choose_manifest");
      if (path) await loadManifest(path);
    } catch (error) {
      showToast("无法选择操作记录", bridgeError(error), true);
    }
  }

  function openSettings() {
    $("#settingsDrawer").classList.add("visible");
    $("#drawerBackdrop").classList.add("visible");
    $("#settingsDrawer").setAttribute("aria-hidden", "false");
  }

  function closeSettings() {
    $("#settingsDrawer").classList.remove("visible");
    $("#drawerBackdrop").classList.remove("visible");
    $("#settingsDrawer").setAttribute("aria-hidden", "true");
  }

  function showLogs() {
    const copy = state.logs.length ? state.logs.join("\n") : "暂无前端活动记录；扫描、执行和导出日志会显示在这里。";
    showToast("运行日志", copy);
  }

  function bindEvents() {
    $$('.filter-pill, .summary-card').forEach((element) => element.addEventListener("click", () => setFilter(element.dataset.filter)));
    $("#searchInput").addEventListener("input", renderResults);
    $("#executePlan").addEventListener("click", openConfirm);
    $("#cancelTask").addEventListener("click", cancelActiveTask);
    $("#cancelModal").addEventListener("click", closeConfirm);
    $("#confirmExecute").addEventListener("click", confirmPendingAction);
    $("#modalBackdrop").addEventListener("click", closeConfirm);
    $$('.mode-option').forEach((element) => element.addEventListener("click", async () => {
      state.mode = element.dataset.mode;
      state.executed = false;
      await rebuildPlan();
    }));
    $("#sidecarToggle").addEventListener("click", async (event) => {
      state.sidecar = !state.sidecar;
      event.currentTarget.classList.toggle("on", state.sidecar);
      if (state.scanId) await rebuildPlan();
    });
    $("#openSettings").addEventListener("click", openSettings);
    $("#closeSettings").addEventListener("click", closeSettings);
    $("#drawerBackdrop").addEventListener("click", closeSettings);
    $("#showLogs").addEventListener("click", showLogs);
    $("#exportReport").addEventListener("click", exportReport);
    $("#loadManifest").addEventListener("click", chooseManifest);
    $("#undoLast").addEventListener("click", () => state.pendingManifest ? loadManifest(state.pendingManifest) : chooseManifest());
    $("#rescanFolder").addEventListener("click", () => startScan(state.root));
    $("#changeFolder").addEventListener("click", chooseFolder);
    $("#chooseFolder").addEventListener("click", chooseFolder);
    $("#showAllDetails").addEventListener("click", () => {
      $(".table-wrap").scrollIntoView({ behavior: "smooth", block: "center" });
      showToast("文件明细", "已定位到当前扫描结果；搜索框可按文件名或路径筛选。");
    });
    $$('.nav-item').forEach((item) => item.addEventListener("click", () => {
      $$('.nav-item').forEach((nav) => nav.classList.toggle("active", nav === item));
      if (item.dataset.nav === "操作记录") chooseManifest();
      else if (item.dataset.nav === "使用说明") showToast("使用说明", "选择目录后自动识别；先预演，再确认执行。所有真实操作都有 manifest，可从操作记录撤销。");
    }));
    $$('.toggle[data-setting-toggle]').forEach((toggle) => toggle.addEventListener("click", async () => {
      const setting = toggle.dataset.settingToggle;
      toggle.classList.toggle("on");
      if (setting === "recursive") {
        state.recursive = toggle.classList.contains("on");
        if (state.root) await startScan(state.root);
      }
      if (setting === "confirm" && !toggle.classList.contains("on")) {
        showToast("安全确认仍保留", "服务端始终要求执行或撤销前明确确认，避免设置误触导致文件被修改。");
        toggle.classList.add("on");
      }
    }));
    $("#conflictPolicy").addEventListener("change", async (event) => {
      state.conflictPolicy = event.target.value;
      if (state.scanId) await rebuildPlan();
    });
    document.addEventListener("dragover", (event) => { event.preventDefault(); $("#dropOverlay").classList.add("visible"); });
    document.addEventListener("dragleave", (event) => { if (event.clientX === 0 && event.clientY === 0) $("#dropOverlay").classList.remove("visible"); });
    document.addEventListener("drop", async (event) => {
      event.preventDefault();
      $("#dropOverlay").classList.remove("visible");
      try {
        const dropped = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
        await startScan(dropped && dropped.path ? dropped.path : await callApi("choose_directory"));
      } catch (error) {
        showToast("无法读取拖入目录", bridgeError(error), true);
      }
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeConfirm();
        closeSettings();
        $("#dropOverlay").classList.remove("visible");
      }
    });
  }

  async function connectBridge() {
    state.api = window.pywebview && window.pywebview.api ? window.pywebview.api : null;
    if (!state.api) {
      setConnection(false);
      updateSummary();
      renderResults();
      updatePlanMode();
      return;
    }
    try {
      const serviceState = await callApi("get_state");
      setConnection(Boolean(serviceState.connected), "Python 核心未响应");
      addLog("Bridge 已连接，等待选择素材文件夹");
    } catch (error) {
      setConnection(false, bridgeError(error));
    }
  }

  bindEvents();
  $("#folderPath").textContent = "尚未选择素材文件夹";
  $("#folderDetail").textContent = "选择目录后自动开始识别";
  setConnection(false);
  updateSummary();
  renderResults();
  updatePlanMode();
  window.addEventListener("pywebviewready", connectBridge);
  connectBridge();
})();
