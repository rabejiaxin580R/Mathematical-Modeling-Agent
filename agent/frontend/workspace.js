/* 统一工作台胶水层：文件浏览器 + 编辑器⇄对话双向联动 + 控制台折叠 + 栏宽拖拽。
   依赖：app.js（聊天逻辑，提供 #input 及其 input 事件监听）、ide.js（提供 window.IDE.editor）。
   本文件不重复声明 app.js/ide.js 的全局，全部封装在 IIFE 内，按 ID 取元素。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const cm = () => (window.IDE && window.IDE.editor) || null;

  // ── 小工具 ──
  function debounce(fn, ms) {
    let t = null;
    return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
  }
  function sepOf(p) { return p.includes("\\") ? "\\" : "/"; }
  function joinPath(dir, name) { return dir.replace(/[\\/]+$/, "") + sepOf(dir) + name; }
  function dirOf(p) { const i = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/")); return i >= 0 ? p.slice(0, i) : p; }
  function modeForExt(name) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    const map = {
      py: "python", md: "markdown", markdown: "markdown",
      tex: "stex", latex: "stex",
      js: "javascript", json: { name: "javascript", json: true },
      html: "htmlmixed", htm: "htmlmixed", xml: "xml", css: "css",
    };
    return map[ext] || null; // 其余按纯文本
  }
  function langForExt(name) {
    const ext = (name.split(".").pop() || "").toLowerCase();
    const map = { py: "python", js: "javascript", json: "json", md: "markdown",
                  csv: "", txt: "", html: "html", htm: "html", css: "css", sql: "sql" };
    return map[ext] !== undefined ? map[ext] : "";
  }

  // 把文本追加进右侧对话输入框，并触发 app.js 的 autoGrow（监听 'input'）
  function appendToInput(text) {
    const input = $("input");
    if (!input) return;
    const cur = input.value;
    input.value = (cur && !cur.endsWith("\n") ? cur + "\n\n" : cur) + text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  // ===== 1. 文件浏览器 =====
  async function fetchTree(path) {
    try {
      const r = await fetch(`/api/ide/tree?path=${encodeURIComponent(path)}`);
      return await r.json();
    } catch (e) { return { ok: false, error: String(e) }; }
  }

  function renderInto(container, data) {
    container.innerHTML = "";
    for (const it of data.items || []) container.appendChild(makeRow(it, data.path));
    if (data.truncated) {
      const note = document.createElement("div");
      note.className = "tree-note";
      note.textContent = `（条目过多，仅显示前 ${(data.items || []).length} 项）`;
      container.appendChild(note);
    }
    if (!(data.items || []).length) {
      const empty = document.createElement("div");
      empty.className = "tree-note";
      empty.textContent = "（空目录）";
      container.appendChild(empty);
    }
  }

  function makeRow(item, parentPath) {
    const full = joinPath(parentPath, item.name);
    const node = document.createElement("div");
    node.className = "tree-node";
    node.dataset.path = full;
    node.dataset.name = item.name;
    node.dataset.isdir = item.is_dir ? "1" : "";

    const row = document.createElement("div");
    row.className = "tree-row";

    const caret = document.createElement("span");
    caret.className = "tree-caret" + (item.is_dir ? "" : " spacer");
    caret.textContent = item.is_dir ? "▸" : "";
    const icon = document.createElement("span");
    icon.className = "tree-icon";
    icon.textContent = item.is_dir ? "📁" : "📄";
    const nameEl = document.createElement("span");
    nameEl.className = "tree-name";
    nameEl.textContent = item.name;
    row.append(caret, icon, nameEl);

    if (item.is_dir) {
      row.title = "点击展开/折叠";
      row.addEventListener("click", () => toggleDir(node, full));
    } else {
      const acts = document.createElement("span");
      acts.className = "tree-actions";
      const defs = [
        ["open", "打开", "打开到编辑器"],
        ["path", "+路径", "把文件路径加入对话"],
        ["content", "+内容", "把文件内容加入对话"],
      ];
      for (const [act, label, title] of defs) {
        const b = document.createElement("button");
        b.className = "t-act"; b.dataset.act = act; b.textContent = label; b.title = title;
        b.addEventListener("click", (e) => {
          e.stopPropagation();
          if (act === "open") openInEditor(full, item.name);
          else if (act === "path") addPathToChat(full);
          else addContentToChat(full, item.name);
        });
        acts.appendChild(b);
      }
      row.appendChild(acts);
    }
    node.appendChild(row);

    if (item.is_dir) {
      const children = document.createElement("div");
      children.className = "tree-children";
      node.appendChild(children);
    }
    return node;
  }

  async function toggleDir(node, path) {
    const children = node.querySelector(":scope > .tree-children");
    if (!node.classList.contains("loaded")) {
      node.classList.add("loaded");
      children.innerHTML = `<div class="tree-note">加载中…</div>`;
      const data = await fetchTree(path);
      if (data && data.ok) renderInto(children, data);
      else {
        children.innerHTML = "";
        const err = document.createElement("div");
        err.className = "tree-error";
        err.textContent = (data && data.error) || "无法读取目录";
        children.appendChild(err);
      }
    }
    node.classList.toggle("open");
  }

  async function loadRoot() {
    const wd = ($("workdir").value || "").trim();
    const tree = $("explorer-tree");
    tree.innerHTML = `<div class="explorer-empty">加载中…</div>`;
    const data = await fetchTree(wd || ".");
    if (!data || !data.ok) {
      tree.innerHTML = "";
      const err = document.createElement("div");
      err.className = "tree-error";
      err.textContent = (data && data.error) || "无法读取该目录";
      tree.appendChild(err);
      return;
    }
    renderInto(tree, data);
  }

  // 过滤（仅作用于当前已加载的节点）
  function filterNode(nodeEl, q) {
    const name = (nodeEl.dataset.name || "").toLowerCase();
    const selfMatch = !q || name.includes(q);
    const children = nodeEl.querySelector(":scope > .tree-children");
    let childMatch = false;
    if (children) {
      children.querySelectorAll(":scope > .tree-node").forEach((c) => {
        if (filterNode(c, q)) childMatch = true;
      });
    }
    const visible = selfMatch || childMatch;
    nodeEl.style.display = visible ? "" : "none";
    if (q && childMatch) nodeEl.classList.add("open");
    return visible;
  }

  // ===== 2. 文件 → 编辑器 / 对话 =====
  async function readFile(path) {
    try {
      const r = await fetch(`/api/ide/read?path=${encodeURIComponent(path)}`);
      return await r.json();
    } catch (e) { return { ok: false, error: String(e) }; }
  }

  async function openInEditor(path, name) {
    const r = await readFile(path);
    if (!r.ok) { alert("打开失败：" + (r.error || "未知错误")); return; }
    const editor = cm();
    if (!editor) return;
    editor.setValue(r.content || "");
    editor.setOption("mode", modeForExt(name));
    const fn = $("filename"); if (fn) fn.value = name;
    const wd = $("workdir"); if (wd) wd.value = dirOf(path);  // 保存/运行回写同一位置
    editor.focus();
  }

  function addPathToChat(path) {
    appendToInput("请阅读这个文件并据此回答：" + path);
  }

  async function addContentToChat(path, name) {
    const r = await readFile(path);
    if (!r.ok) { alert("读取失败：" + (r.error || "未知错误")); return; }
    appendToInput("文件 `" + path + "` 的内容：\n```" + langForExt(name) + "\n" + (r.content || "") + "\n```");
  }

  // ===== 3. 编辑器选区 → 对话 =====
  function insertSelection() {
    const editor = cm();
    if (!editor) return;
    const sel = editor.getSelection() || editor.getValue();
    if (!sel.trim()) return;
    const m = editor.getOption("mode");
    const lang = typeof m === "string" ? m : "";
    appendToInput("```" + lang + "\n" + sel + "\n```");
  }

  // ===== 4. AI 回复代码块 → 编辑器 =====
  const enhanceCodeBlocks = debounce(() => {
    document.querySelectorAll("#messages pre").forEach((pre) => {
      if (pre.querySelector(".to-editor-btn")) return;
      const btn = document.createElement("button");
      btn.className = "to-editor-btn";
      btn.textContent = "→编辑器";
      btn.title = "把这段代码放进左侧编辑器";
      btn.addEventListener("click", () => {
        const code = pre.querySelector("code");
        const text = code ? code.innerText : pre.innerText;
        const editor = cm();
        if (editor) { editor.setValue(text); editor.focus(); }
      });
      pre.appendChild(btn);
    });
  }, 150);

  // AI 调 write_file 落盘成功 → 自动把该文件加载进中栏编辑器（实时看到 AI 写出的文件）
  window.addEventListener("ai-file-write", (e) => {
    const path = e && e.detail && e.detail.path;
    if (!path) return;
    const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "file";
    openInEditor(path, name);
  });

  // ===== 5. 控制台折叠 =====
  function setupConsoleToggle() {
    const btn = $("console-toggle");
    const con = $("ws-console");
    if (!btn || !con) return;
    btn.addEventListener("click", () => {
      con.classList.toggle("collapsed");
      btn.textContent = con.classList.contains("collapsed") ? "▴" : "▾";
      const editor = cm(); if (editor) setTimeout(() => editor.refresh(), 50);
    });
  }

  // ===== 6. 栏宽拖拽 =====
  function setupGutters() {
    const ws = $("ws");
    const lv = localStorage.getItem("ws-col-left");
    const rv = localStorage.getItem("ws-col-right");
    if (lv) ws.style.setProperty("--col-left", lv + "px");
    if (rv) ws.style.setProperty("--col-right", rv + "px");

    document.querySelectorAll(".ws-gutter").forEach((g) => {
      g.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const target = g.dataset.target;
        const startX = e.clientX;
        const cs = getComputedStyle(ws);
        const startLeft = parseInt(cs.getPropertyValue("--col-left")) || 240;
        const startRight = parseInt(cs.getPropertyValue("--col-right")) || 440;
        document.body.style.cursor = "col-resize";
        document.body.style.userSelect = "none";

        const move = (ev) => {
          const d = ev.clientX - startX;
          if (target === "left") {
            const v = Math.min(480, Math.max(160, startLeft + d));
            ws.style.setProperty("--col-left", v + "px");
            localStorage.setItem("ws-col-left", v);
          } else {
            const v = Math.min(760, Math.max(300, startRight - d));
            ws.style.setProperty("--col-right", v + "px");
            localStorage.setItem("ws-col-right", v);
          }
          const editor = cm(); if (editor) editor.refresh();
        };
        const up = () => {
          document.removeEventListener("mousemove", move);
          document.removeEventListener("mouseup", up);
          document.body.style.cursor = "";
          document.body.style.userSelect = "";
        };
        document.addEventListener("mousemove", move);
        document.addEventListener("mouseup", up);
      });
    });
  }

  // ===== 初始化 =====
  $("workdir").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); loadRoot(); }
  });
  $("explorer-refresh").addEventListener("click", loadRoot);
  $("explorer-filter").addEventListener("input", () => {
    const q = $("explorer-filter").value.trim().toLowerCase();
    document.querySelectorAll("#explorer-tree > .tree-node").forEach((n) => filterNode(n, q));
  });
  $("btn-insert-sel").addEventListener("click", insertSelection);

  setupConsoleToggle();
  setupGutters();
  setupLayout();

  // 布局预设 + 面板显隐（layout.js）
  function setupLayout() {
    if (!window.MMLayout) return;
    MMLayout.init({
      containerSel: "#ws",
      gridSel: ".ws-grid",
      mountSel: ".ws-brand",
      storageKey: "ws-layout",
      gutterSel: ".ws-gutter",
      regions: [
        { key: "left",  sel: ".ws-left",  label: "文件树", fixed: "var(--col-left, 240px)" },
        { key: "mid",   sel: ".ws-mid",   label: "编辑器/终端", flexible: true },
        { key: "right", sel: ".ws-right", label: "AI 对话", fixed: "var(--col-right, 440px)" },
      ],
      presets: [
        { key: "default",      label: "默认（文件｜编辑｜对话）", order: ["left", "mid", "right"], wide: "mid",   hidden: [] },
        { key: "chat-center",  label: "对话居中",                 order: ["left", "right", "mid"], wide: "right", hidden: [] },
        { key: "focus-editor", label: "专注编辑（隐藏对话）",     order: ["left", "mid"],          wide: "mid",   hidden: ["right"] },
        { key: "focus-chat",   label: "聚焦对话（隐藏文件树）",   order: ["mid", "right"],         wide: "right", hidden: ["left"] },
      ],
      onChange: () => { const ed = cm(); if (ed) ed.refresh(); },
    });
  }

  // 从 /build/free?ask=... 预填对话输入框（来自学习模式「带这个知识点去问助教」）
  (function prefillAsk() {
    const q = new URLSearchParams(location.search).get("ask");
    if (!q) return;
    const inp = document.getElementById("input");
    if (!inp) return;
    inp.value = q;
    inp.dispatchEvent(new Event("input", { bubbles: true }));
    inp.focus();
    // 清掉地址栏 query，避免刷新重复预填
    history.replaceState(null, "", "/build/free");
  })();

  const mo = new MutationObserver(enhanceCodeBlocks);
  mo.observe($("messages"), { childList: true, subtree: true });
  enhanceCodeBlocks();

  // ===== 首次使用 · 工作目录选择弹窗 =====
  (function initFolderModal() {
    const STORAGE_KEY = "mm_folder_setup_done";
    if (localStorage.getItem(STORAGE_KEY) === "1") return;

    const overlay = document.getElementById("fm-overlay");
    if (!overlay) return;
    overlay.hidden = false;

    // Tab 切换
    const tabs = overlay.querySelectorAll(".fm-tab");
    const panelNew = document.getElementById("fm-panel-new");
    const panelExisting = document.getElementById("fm-panel-existing");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        var target = tab.dataset.tab;
        if (target === "new") {
          panelNew.hidden = false;
          panelExisting.hidden = true;
        } else {
          panelNew.hidden = true;
          panelExisting.hidden = false;
        }
      });
    });

    // 完成：设置工作目录，加载文件树，关闭弹窗
    function finish(dir) {
      var wd = document.getElementById("workdir");
      if (wd) { wd.value = dir; }
      localStorage.setItem(STORAGE_KEY, "1");
      overlay.hidden = true;
      // 触发加载文件树
      if (wd) { wd.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })); }
    }

    // 新建文件夹
    document.getElementById("fm-btn-create").addEventListener("click", function () {
      var name = document.getElementById("fm-new-name").value.trim();
      var parent = document.getElementById("fm-new-parent").value.trim();
      var msgEl = document.getElementById("fm-msg-new");

      if (!name) { msgEl.textContent = "请输入文件夹名称"; return; }

      // 默认父目录：桌面
      if (!parent) {
        var home = (function () {
          var h = "";
          try { h = process && process.env ? (process.env.USERPROFILE || process.env.HOME || "") : ""; } catch (_) {}
          return h;
        })();
        parent = home ? home + "\\Desktop" : "C:\\";
      }

      var fullPath = parent.replace(/[\\/]+$/, "") + "\\" + name;

      fetch("/api/ide/mkdir", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: fullPath }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            finish(fullPath);
          } else {
            msgEl.textContent = "创建失败：" + (data.error || "未知错误");
          }
        })
        .catch(function (e) {
          msgEl.textContent = "请求失败：" + e.message;
        });
    });

    // 打开已有文件夹
    document.getElementById("fm-btn-open").addEventListener("click", function () {
      var path = document.getElementById("fm-existing-path").value.trim();
      var msgEl = document.getElementById("fm-msg-existing");

      if (!path) { msgEl.textContent = "请输入文件夹路径"; return; }

      // 验证路径：尝试列出目录
      fetch("/api/ide/tree?path=" + encodeURIComponent(path))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) {
            finish(path);
          } else {
            msgEl.textContent = "无法访问该目录：" + (data.error || "请检查路径是否正确");
          }
        })
        .catch(function (e) {
          msgEl.textContent = "请求失败：" + e.message;
        });
    });

    // 回车快捷提交
    document.getElementById("fm-new-name").addEventListener("keydown", function (e) {
      if (e.key === "Enter") document.getElementById("fm-btn-create").click();
    });
    document.getElementById("fm-existing-path").addEventListener("keydown", function (e) {
      if (e.key === "Enter") document.getElementById("fm-btn-open").click();
    });

    // 跳过：使用临时目录
    document.getElementById("fm-skip").addEventListener("click", function () {
      localStorage.setItem(STORAGE_KEY, "1");
      overlay.hidden = true;
    });
  })();
})();
