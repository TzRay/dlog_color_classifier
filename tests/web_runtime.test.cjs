/**
 * 运行生产 app.js 的交互回归，不依赖第三方 DOM 包或真实视频素材。
 * DOM 替身只提供页面使用的标准接口；API 响应与时钟可控，以覆盖桥接竞态。
 * 执行：node --test tests/web_runtime.test.cjs
 */
"use strict";

const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const html = readFileSync(path.join(root, "prototype/index.html"), "utf8");
const script = readFileSync(path.join(root, "prototype/app.js"), "utf8");
const settle = () => new Promise((resolve) => setImmediate(resolve));

class Element {
  constructor(attributes = {}) {
    this.attributes = { ...attributes };
    this.children = [];
    this.handlers = new Map();
    this.dataset = {};
    this.style = {};
    this.value = attributes.value || "";
    this.hidden = "hidden" in attributes;
    this.disabled = "disabled" in attributes;
    this.checked = "checked" in attributes;
    this.textContent = "";
    this.lastChild = {};
    const classes = new Set((attributes.class || "").split(/\s+/));
    this.classList = {
      add: (name) => classes.add(name),
      remove: (name) => classes.delete(name),
      contains: (name) => classes.has(name),
      toggle(name, force = !classes.has(name)) { force ? classes.add(name) : classes.delete(name); },
    };
    for (const [name, value] of Object.entries(attributes)) {
      if (name.startsWith("data-")) this.dataset[name.slice(5)] = value;
    }
  }

  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name]; }
  removeAttribute(name) { delete this.attributes[name]; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  insertCell() { return this.appendChild(new Element()); }
  querySelector(selector) {
    if (selector === ".summary-value") return this.summary || (this.summary = new Element());
    throw new Error(`测试 DOM 不支持选择器：${selector}`);
  }
  addEventListener(type, callback) {
    const handlers = this.handlers.get(type) || [];
    handlers.push(callback);
    this.handlers.set(type, handlers);
  }
  emit(type, event = {}) { return Promise.all((this.handlers.get(type) || []).map((callback) => callback(event))); }
  focus() {}
  select() {}
  showModal() { this.hidden = false; }
  close() { this.hidden = true; }
}

/** 根据真实 HTML 登记控件，新增 JS 选择器若没有对应节点会立即失败。 */
function createHarness(overrides = {}) {
  const elements = [];
  const byId = new Map();
  for (const match of html.matchAll(/<[a-z][a-z0-9-]*\b([^<>]*)>/gi)) {
    const attributes = {};
    for (const attribute of match[1].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      attributes[attribute[1]] = attribute[2] ?? "";
    }
    const element = new Element(attributes);
    elements.push(element);
    if (attributes.id) byId.set(attributes.id, element);
  }
  const get = (id) => {
    assert.ok(byId.has(id), `页面缺少控件：${id}`);
    return byId.get(id);
  };
  get("conflictPolicy").value = "suffix";
  const document = new Element();
  Object.assign(document, {
    querySelector(selector) {
      if (selector.startsWith("#")) return get(selector.slice(1));
      if (selector === 'input[name="exportFormat"]:checked') return elements.find((element) => element.attributes.name === "exportFormat" && element.checked);
      throw new Error(`测试 DOM 不支持选择器：${selector}`);
    },
    querySelectorAll(selector) {
      if (selector === "[data-filter]") return elements.filter((element) => "filter" in element.dataset);
      if (selector === ".mode-option") return elements.filter((element) => element.classList.contains("mode-option"));
      throw new Error(`测试 DOM 不支持选择器：${selector}`);
    },
    createElement: () => new Element(),
    createTextNode: (text) => ({ textContent: text }),
  });

  let now = 0;
  let timerId = 0;
  const timers = new Map();
  const calls = { scan: [], organize: [], cancel: [], status: [] };
  const snapshots = new Map();
  const api = {
    get_state: async () => ({ connected: true }),
    start_scan: async () => ({ task_id: "scan_task" }),
    execute_organize: async () => ({ task_id: "organize_task" }),
    cancel_task: async () => ({}),
    get_task_status: async (id) => {
      assert.ok(snapshots.has(id), `测试未配置任务状态：${id}`);
      return snapshots.get(id);
    },
    ...overrides,
  };
  const recorded = (name, label) => async (payload) => {
    calls[label].push(payload);
    return api[name](payload);
  };
  const window = new Element();
  Object.assign(window, {
    innerWidth: 1240,
    setTimeout(callback, delay) {
      timers.set(++timerId, { callback, due: now + delay });
      return timerId;
    },
    clearTimeout: (id) => timers.delete(id),
    pywebview: { api: {
      ...api,
      start_scan: recorded("start_scan", "scan"),
      execute_organize: recorded("execute_organize", "organize"),
      cancel_task: recorded("cancel_task", "cancel"),
      get_task_status: recorded("get_task_status", "status"),
    } },
  });
  vm.runInNewContext(script, { window, document, console }, { filename: "prototype/app.js" });

  return {
    api, calls, snapshots, get, document, window,
    click(id) { return get(id).disabled ? Promise.resolve() : get(id).emit("click"); },
    filter(mode) { return document.querySelectorAll("[data-filter]").find((item) => item.dataset.filter === mode).emit("click"); },
    async advance(milliseconds) {
      const target = now + milliseconds;
      while (true) {
        const next = [...timers].sort((a, b) => a[1].due - b[1].due)[0];
        if (!next || next[1].due > target) break;
        now = next[1].due;
        timers.delete(next[0]);
        next[1].callback();
        await settle();
      }
      now = target;
      await settle();
    },
  };
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function scanResult(count = 1) {
  return {
    scan_id: "scan_result", root: "D:\\素材", recursive: true,
    results: Array.from({ length: count }, (_, index) => ({
      name: `DJI_${String(index).padStart(5, "0")}.MP4`,
      relative_path: `素材/DJI_${String(index).padStart(5, "0")}.MP4`,
      path: `D:\\素材\\DJI_${index}.MP4`,
      mode: index % 2 ? "rec709" : "dlog", status: "ready", folder: "当前目录",
    })),
  };
}

async function finishScan(harness, count = 1) {
  harness.snapshots.set("scan_task", { state: "completed", result: scanResult(count) });
  harness.window.djiColorDeskHandleDrop("D:\\素材");
  await settle();
  assert.equal(harness.get("executeOrganize").disabled, false);
}

test("扫描提交期间锁定入口，运行时可取消，取消后不保留不完整扫描", async () => {
  const submission = deferred();
  const harness = createHarness({ start_scan: () => submission.promise });
  harness.window.djiColorDeskHandleDrop("D:\\素材");
  harness.window.djiColorDeskHandleDrop("D:\\素材");
  assert.equal(harness.calls.scan.length, 1);
  assert.equal(harness.get("chooseFolder").disabled, true);
  assert.equal(harness.get("cancelTask").hidden, true);

  harness.snapshots.set("scan_task", { state: "queued", completed: 0, total: 0, message: "等待开始" });
  submission.resolve({ task_id: "scan_task" });
  await settle();
  assert.equal(harness.get("cancelTask").hidden, false);
  assert.equal(harness.get("cancelTask").disabled, false);
  assert.equal(harness.get("taskProgress").getAttribute("aria-valuenow"), undefined);

  harness.snapshots.set("scan_task", { state: "running", completed: 2, total: 5, message: "正在识别" });
  await harness.advance(180);
  assert.match(harness.get("folderDetail").textContent, /2 \/ 5/);
  assert.equal(harness.get("taskProgressValue").style.width, "40%");
  assert.equal(harness.get("taskProgress").getAttribute("aria-valuenow"), "2");
  await harness.click("cancelTask");
  assert.deepEqual(harness.calls.cancel, ["scan_task"]);
  assert.equal(harness.get("cancelTask").disabled, true);
  await harness.click("cancelTask");
  assert.equal(harness.calls.cancel.length, 1);

  harness.snapshots.set("scan_task", { state: "cancelled", result: { cancelled: true } });
  await harness.advance(180);
  assert.equal(harness.get("cancelTask").hidden, true);
  assert.equal(harness.get("chooseFolder").disabled, false);
  assert.equal(harness.get("executeOrganize").disabled, true);
  assert.equal(harness.get("exportReport").disabled, true);
});

test("整理运行时可取消，展示部分结果及未执行数量，再整理必须重新识别", async () => {
  const submission = deferred();
  const harness = createHarness({ execute_organize: () => submission.promise });
  await finishScan(harness, 3);
  const operation = harness.click("executeOrganize");
  assert.equal(harness.get("executeOrganize").disabled, true);
  assert.equal(harness.get("cancelTask").hidden, true);
  harness.snapshots.set("organize_task", { state: "running", completed: 1, total: 3, message: "正在复制" });
  submission.resolve({ task_id: "organize_task" });
  await settle();
  assert.equal(harness.get("cancelTask").hidden, false);
  assert.match(harness.get("folderDetail").textContent, /1 \/ 3/);
  await harness.click("cancelTask");
  harness.snapshots.set("organize_task", {
    state: "cancelled", completed: 1, total: 3,
    result: { cancelled: true, success_count: 1, skipped_count: 0, failed_count: 0, pending_count: 2, records: [] },
  });
  await harness.advance(180);
  await operation;
  assert.deepEqual(harness.calls.cancel, ["organize_task"]);
  assert.equal(harness.get("successCount").textContent, "1");
  assert.equal(harness.get("pendingCount").textContent, "2");
  assert.equal(harness.get("outcomePanel").hidden, false);
  assert.equal(harness.get("executeOrganize").disabled, true);
  assert.equal(harness.get("rescanFolder").disabled, false);
  await harness.get("executeOrganize").emit("click");
  assert.equal(harness.calls.organize.length, 1);
  await finishScan(harness);
});

test("轮询通信中断保持任务锁，连接恢复后接受终态", async () => {
  const harness = createHarness();
  await finishScan(harness);
  harness.api.get_task_status = async () => { throw new Error("模拟桥接中断"); };
  const operation = harness.click("executeOrganize");
  await settle();
  assert.equal(harness.get("rescanFolder").disabled, true);
  assert.equal(harness.get("executeOrganize").disabled, true);
  assert.equal(harness.get("cancelTask").hidden, false);
  assert.match(harness.get("folderDetail").textContent, /重试/);
  harness.window.djiColorDeskHandleDrop("D:\\其他素材");
  assert.equal(harness.calls.scan.length, 1);
  harness.api.get_task_status = async () => ({ state: "completed", result: { success_count: 1, records: [] } });
  await harness.advance(1000);
  await operation;
  assert.equal(harness.get("rescanFolder").disabled, false);
  assert.equal(harness.get("executeOrganize").disabled, true);
  assert.equal(harness.get("cancelTask").hidden, true);
  assert.equal(harness.get("connectionStatus").classList.contains("connected"), true);
});

test("提交失败和执行失败都阻止复用可能已变更的扫描结果", async () => {
  for (const failure of ["submission", "execution"]) {
    const harness = createHarness();
    await finishScan(harness);
    if (failure === "submission") {
      harness.api.execute_organize = async () => { throw new Error("提交响应中断"); };
    } else {
      harness.snapshots.set("organize_task", { state: "failed", error: "整理异常" });
    }
    await harness.click("executeOrganize");
    assert.equal(harness.get("executeOrganize").disabled, true);
    assert.equal(harness.get("rescanFolder").disabled, false);
    assert.match(harness.get("folderDetail").textContent, /重新识别/);
    await harness.get("executeOrganize").emit("click");
    assert.equal(harness.calls.organize.length, 1);
  }
});

test("取消失败可重试，迟到的取消响应不会覆盖已完成结果", async () => {
  const cancellation = deferred();
  const harness = createHarness({ cancel_task: () => cancellation.promise });
  harness.snapshots.set("scan_task", { state: "running", completed: 0, total: 1 });
  harness.window.djiColorDeskHandleDrop("D:\\素材");
  await settle();
  const firstCancel = harness.click("cancelTask");
  cancellation.reject(new Error("取消请求失败"));
  await firstCancel;
  assert.equal(harness.get("cancelTask").disabled, false);

  const delayed = deferred();
  harness.api.cancel_task = () => delayed.promise;
  const secondCancel = harness.click("cancelTask");
  harness.snapshots.set("scan_task", { state: "completed", result: scanResult() });
  await harness.advance(180);
  delayed.resolve({});
  await secondCancel;
  assert.equal(harness.get("toastTitle").textContent, "识别完成");
  assert.equal(harness.get("executeOrganize").disabled, false);
});

test("大量结果限制每页行数，分页无遗漏，搜索防抖且筛选重置页码", async () => {
  const harness = createHarness();
  await finishScan(harness, 1205);
  const body = harness.get("resultBody");
  assert.equal(body.children.length, 100);
  assert.equal(harness.get("pageNumber").textContent, "1 / 13");
  const names = [];
  do {
    names.push(...body.children.map((row) => row.children[1].textContent));
    if (harness.get("nextPage").disabled) break;
    await harness.click("nextPage");
    assert.ok(body.children.length <= 100);
  } while (true);
  assert.equal(names.length, 1205);
  assert.equal(new Set(names).size, 1205);
  assert.equal(harness.get("pageNumber").textContent, "13 / 13");
  await harness.click("previousPage");
  assert.equal(harness.get("pageNumber").textContent, "12 / 13");

  harness.get("searchInput").value = "不存在";
  await harness.get("searchInput").emit("input");
  await harness.advance(100);
  harness.get("searchInput").value = "DJI_01204";
  await harness.get("searchInput").emit("input");
  await harness.advance(100);
  assert.equal(harness.get("pageNumber").textContent, "12 / 13");
  await harness.advance(80);
  assert.equal(body.children.length, 1);
  assert.equal(body.children[0].children[1].textContent, "DJI_01204.MP4");
  assert.equal(harness.get("pageNumber").textContent, "1 / 1");

  harness.get("searchInput").value = "";
  await harness.filter("rec709");
  assert.equal(harness.get("pageNumber").textContent, "1 / 7");
  assert.match(harness.get("tableCount").textContent, /匹配 602 项/);
  assert.equal(body.children[0].children[1].textContent, "DJI_00001.MP4");
  harness.get("searchInput").value = "未匹配的文件";
  await harness.get("searchInput").emit("input");
  await harness.advance(180);
  assert.equal(body.children[0].children[0].textContent, "没有匹配的文件");
  assert.equal(harness.get("nextPage").disabled, true);
});

test("任务期间刷新快捷键不会重载页面或发起第二次扫描", async () => {
  const harness = createHarness();
  harness.snapshots.set("scan_task", { state: "running" });
  harness.window.djiColorDeskHandleDrop("D:\\素材");
  await settle();
  for (const key of ["F5", "r"]) {
    let prevented = false;
    await harness.document.emit("keydown", { key, ctrlKey: key === "r", preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
  }
  assert.equal(harness.calls.scan.length, 1);
});

test("重新扫描清空旧搜索和筛选，并回到第一页", async () => {
  const harness = createHarness();
  await finishScan(harness, 350);
  await harness.filter("dlog");
  await harness.click("nextPage");
  harness.get("searchInput").value = "不存在";
  await harness.get("searchInput").emit("input");
  harness.snapshots.set("scan_task", { state: "completed", result: scanResult(350) });
  await harness.click("rescanFolder");
  await harness.advance(180);
  assert.equal(harness.get("searchInput").value, "");
  assert.equal(harness.get("pageNumber").textContent, "1 / 4");
  assert.match(harness.get("tableCount").textContent, /匹配 350 项/);
  assert.equal(harness.get("resultBody").children[1].children[1].textContent, "DJI_00001.MP4");
});
