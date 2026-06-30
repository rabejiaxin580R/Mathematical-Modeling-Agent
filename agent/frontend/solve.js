/* 做题驾驶舱：上传/粘贴题面 → AI 拆步 → 点开每一步，和「建模共创伙伴」一起做。
   独立于自由练习：自己的页面、布局、后端人格（/api/solve/*）。
   复用：MMRender（render.js）渲染 Markdown/公式；Profile（profile.js）做门禁。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MOD = { "key-points": "要点拆解", formula: "公式建模", code: "编程求解", prose: "分析论述" };

  // ── 状态 ──
  let runId = null;          // 上传题目文件所在的工作目录（= 一个会话 id）
  const files = [];          // 已上传的题目文件名
  let problem = null;        // 拆步后的题目对象
  let problemConvId = null;  // 这道题的做题会话（一题一块上下文）
  let curStepIdx = -1;
  const seen = new Set();
  let streaming = false;
  let abort = null;
  let solveSessionId = null;       // 做题存档 id
  const stepConvs = {};            // step_id -> 该步的做题会话 id（恢复时接得上）

  // ── 门禁 ──
  Profile.require().then((p) => { if (p) init(); });

  function init() {
    bindSetup();
    bindComposer();
    setupEditorBridge();
    setupSettings();
    setupModelMenu();
    loadSettings();
    loadArchive();
    setupLayout();
  }

  // 布局预设 + 面板显隐（layout.js）
  function setupLayout() {
    if (!window.MMLayout) return;
    MMLayout.init({
      containerSel: "#sv",
      gridSel: ".sv-cockpit",
      mountSel: ".sv-brand",
      storageKey: "sv-layout",
      regions: [
        { key: "rail", sel: ".sv-rail",    label: "路线图", fixed: "300px" },
        { key: "mid",  sel: ".sv-col-mid", label: "编辑器/终端", flexible: true },
        { key: "chat", sel: ".sv-stage",   label: "共创对话", fixed: "440px" },
      ],
      presets: [
        { key: "default",      label: "默认（路线｜编辑｜对话）", order: ["rail", "mid", "chat"], wide: "mid",  hidden: [] },
        { key: "chat-center",  label: "对话居中",                 order: ["rail", "chat", "mid"], wide: "chat", hidden: [] },
        { key: "focus-editor", label: "专注编辑（隐藏对话）",     order: ["rail", "mid"],         wide: "mid",  hidden: ["chat"] },
        { key: "focus-chat",   label: "聚焦对话（隐藏路线图）",   order: ["mid", "chat"],         wide: "chat", hidden: ["rail"] },
      ],
      onChange: () => { const ed = window.IDE && window.IDE.editor; if (ed) ed.refresh(); },
    });
  }

  // 编辑器 ⇄ 对话联动（编辑器/终端由 ide.js 提供：window.IDE）
  function appendToInput(text) {
    const input = $("sv-input");
    if (!input || input.disabled) return;
    const cur = input.value;
    input.value = (cur && !cur.endsWith("\n") ? cur + "\n\n" : cur) + text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function setupEditorBridge() {
    // 选中编辑器代码 → 插入对话输入框
    const ins = $("btn-insert-sel");
    if (ins) ins.addEventListener("click", () => {
      const ed = window.IDE && window.IDE.editor;
      if (!ed) return;
      const sel = ed.getSelection() || ed.getValue();
      if (!sel.trim()) return;
      appendToInput("```python\n" + sel + "\n```");
    });
    // 控制台折叠
    const tog = $("console-toggle");
    const con = $("ws-console");
    if (tog && con) tog.addEventListener("click", () => {
      con.classList.toggle("collapsed");
      tog.textContent = con.classList.contains("collapsed") ? "▴" : "▾";
      const ed = window.IDE && window.IDE.editor;
      if (ed) setTimeout(() => ed.refresh(), 50);
    });
    // AI 回复里的代码块 → 一键放进编辑器
    let t = null;
    const enhance = () => {
      clearTimeout(t);
      t = setTimeout(() => {
        $("sv-chat").querySelectorAll("pre").forEach((pre) => {
          if (pre.querySelector(".to-editor-btn")) return;
          const btn = document.createElement("button");
          btn.className = "to-editor-btn";
          btn.textContent = "→编辑器";
          btn.title = "把这段代码放进中间编辑器";
          btn.addEventListener("click", () => {
            const code = pre.querySelector("code");
            const text = code ? code.innerText : pre.innerText;
            const ed = window.IDE && window.IDE.editor;
            if (ed) { ed.setValue(text); ed.focus(); }
          });
          pre.appendChild(btn);
        });
      }, 120);
    };
    new MutationObserver(enhance).observe($("sv-chat"), { childList: true, subtree: true });
  }

  // ========== 视图 A：立题（上传 / 粘贴 → 拆步） ==========
  function bindSetup() {
    const fileInput = $("sv-file");
    const chips = $("sv-chips");

    $("sv-upload").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      const chip = document.createElement("span");
      chip.className = "sv-chip";
      const label = document.createElement("span");
      label.textContent = "⏳ " + f.name;
      chip.appendChild(label);
      chips.appendChild(chip);
      try {
        const fd = new FormData();
        fd.append("file", f);
        if (runId) fd.append("conversation_id", runId);
        const r = await fetch("/api/upload", { method: "POST", body: fd });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          label.textContent = "✗ " + (err.detail || f.name); chip.classList.add("err");
          return;
        }
        const d = await r.json();
        runId = d.conversation_id;
        if (!files.includes(d.filename)) files.push(d.filename);
        label.textContent = "📎 " + d.filename;
        const del = document.createElement("button");
        del.className = "sv-chip-del"; del.title = "删除这个文件"; del.textContent = "×";
        del.addEventListener("click", async () => {
          const i = files.indexOf(d.filename);
          if (i >= 0) files.splice(i, 1);
          chip.remove();
          if (runId) {
            try {
              await fetch("/api/upload/remove", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ conversation_id: runId, filename: d.filename }),
              });
            } catch { /* 列表已移除即可 */ }
          }
        });
        chip.appendChild(del);
      } catch {
        label.textContent = "✗ " + f.name; chip.classList.add("err");
      }
    });

    $("sv-analyze").addEventListener("click", analyze);
    $("sv-reset").addEventListener("click", resetProblem);
  }

  async function analyze() {
    const statement = ($("sv-statement").value || "").trim();
    const status = $("sv-status");
    if (!statement && !files.length) { status.textContent = "先粘贴题面或上传题目文件"; return; }
    const btn = $("sv-analyze");
    btn.disabled = true;
    status.textContent = "助教正在读题、拆解解题路线…（约需十几秒）";
    try {
      const r = await fetch("/api/build/problems/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statement, run_id: runId || "", files }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        status.textContent = "拆解失败：" + (err.detail || r.status);
        return;
      }
      problem = await r.json();
      solveSessionId = null;              // 新题：建一份新存档
      Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
      renderProblem();
      saveArchive();
      status.textContent = "";
    } catch (e) {
      status.textContent = "请求出错：" + e;
    } finally {
      btn.disabled = false;
    }
  }

  function resetProblem() {
    if (streaming && abort) abort.abort();
    problem = null; problemConvId = null; curStepIdx = -1; seen.clear();
    solveSessionId = null;
    Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
    if (window.MMAnim) MMAnim.transitionView($("sv-cockpit"), $("sv-setup"));
    else { $("sv-cockpit").hidden = true; $("sv-setup").hidden = false; }
    $("sv-reset").hidden = true;
    $("sv-cur-title").textContent = "";
    $("sv-chat").innerHTML = "";
    setComposerEnabled(false);
    $("sv-step-head").innerHTML = `<div class="sv-step-empty">从左边的路线里点开一步，开始和助教一起做。</div>`;
    loadArchive();
  }

  // ========== 做题存档（题目快照 + 进度 + 每步对话，刷新/下次进来可继续） ==========
  function saveArchive() {
    if (!problem) return;
    const body = {
      id: solveSessionId || "",
      title: problem.title || "我的题目",
      problem,
      run_id: runId || "",
      files,
      progress: { seen: Array.from(seen), cur_step: curStepIdx },
      step_convs: stepConvs,
    };
    fetch("/api/solve/sessions", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => { if (d && d.id) solveSessionId = d.id; })
      .catch(() => { /* 存档失败不打断做题 */ });
  }

  async function loadArchive() {
    const box = $("sv-archive");
    const list = $("sv-archive-list");
    if (!box || !list) return;
    try {
      const items = await (await fetch("/api/solve/sessions")).json();
      if (!Array.isArray(items) || !items.length) { box.hidden = true; return; }
      list.innerHTML = "";
      items.forEach((it) => list.appendChild(archiveRow(it)));
      box.hidden = false;
    } catch { box.hidden = true; }
  }

  function archiveRow(it) {
    const row = document.createElement("div");
    row.className = "sv-arch-item";
    const when = it.updated_at ? new Date(it.updated_at * 1000).toLocaleString("zh-CN", { hour12: false }) : "";
    row.innerHTML =
      `<button class="sv-arch-main" title="继续这道题">` +
        `<span class="sv-arch-title">${MMRender.escapeHtml(it.title || "我的题目")}</span>` +
        `<span class="sv-arch-meta">进度 ${it.seen_count || 0}/${it.total_steps || 0} · ${MMRender.escapeHtml(when)}</span>` +
      `</button>` +
      `<button class="sv-arch-del" title="删除这份存档">×</button>`;
    row.querySelector(".sv-arch-main").addEventListener("click", () => restoreSession(it.id));
    row.querySelector(".sv-arch-del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("删除这份做题存档？该题的对话记录也会一并删除。")) return;
      try { await fetch("/api/solve/sessions/" + encodeURIComponent(it.id), { method: "DELETE" }); } catch { /* 忽略 */ }
      loadArchive();
    });
    return row;
  }

  async function restoreSession(id) {
    let data;
    try {
      const r = await fetch("/api/solve/sessions/" + encodeURIComponent(id));
      if (!r.ok) throw new Error();
      data = await r.json();
    } catch { alert("打开存档失败"); return; }
    if (!data || !data.problem) { alert("存档已损坏"); return; }
    solveSessionId = data.id;
    problem = data.problem;
    runId = data.run_id || null;
    files.length = 0;
    (data.files || []).forEach((f) => files.push(f));
    Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
    Object.assign(stepConvs, data.step_convs || {});
    renderProblem();
    // 回填已看过的步骤标记
    seen.clear();
    ((data.progress || {}).seen || []).forEach((i) => seen.add(Number(i)));
    paintRoadmap();
  }

  // ========== 视图 B：解题驾驶舱（路线图 + 当前步共创） ==========
  function renderProblem() {
    curStepIdx = -1; problemConvId = null; seen.clear();
    if (window.MMAnim) MMAnim.transitionView($("sv-setup"), $("sv-cockpit"));
    else { $("sv-setup").hidden = true; $("sv-cockpit").hidden = false; }
    $("sv-reset").hidden = false;
    $("sv-cur-title").textContent = problem.title || "我的题目";
    $("sv-rail-title").textContent = problem.title || "我的题目";

    const bg = $("sv-bg");
    MMRender.renderMarkdown(bg, problem.background || "");
    bg.hidden = true;
    const bgToggle = $("sv-bg-toggle");
    bgToggle.onclick = () => {
      bg.hidden = !bg.hidden;
      bgToggle.textContent = bg.hidden ? "题面 ▾" : "题面 ▴";
    };

    renderRoadmap();

    $("sv-chat").innerHTML = "";
    $("sv-step-head").innerHTML = `<div class="sv-step-empty">从左边的路线里点开一步，开始和助教一起做。</div>`;
    setComposerEnabled(false);
  }

  function renderRoadmap() {
    const wrap = $("sv-roadmap");
    wrap.innerHTML = "";
    (problem.steps || []).forEach((step, idx) => {
      const node = document.createElement("button");
      node.className = "sv-step press";
      node.dataset.idx = String(idx);
      const mlabel = MOD[step.modality] || "";
      node.innerHTML =
        `<span class="sv-step-num">${idx + 1}</span>` +
        `<span class="sv-step-body">` +
          `<span class="sv-step-title">${MMRender.escapeHtml(step.title || "第 " + (idx + 1) + " 步")}` +
            (mlabel ? ` <span class="sv-step-mod">${mlabel}</span>` : "") +
          `</span>` +
        `</span>`;
      node.addEventListener("click", () => enterStep(idx));
      wrap.appendChild(node);
    });
    if (window.MMAnim) MMAnim.reveal(wrap, { observe: false });
    paintRoadmap();
  }

  function paintRoadmap() {
    $("sv-roadmap").querySelectorAll(".sv-step").forEach((n) => {
      const i = Number(n.dataset.idx);
      n.classList.toggle("cur", i === curStepIdx);
      n.classList.toggle("seen", seen.has(i));
    });
  }

  async function enterStep(idx) {
    if (!problem) return;
    if (streaming && abort) abort.abort();
    const step = problem.steps[idx];
    curStepIdx = idx;
    seen.add(idx);
    paintRoadmap();

    // 本步任务卡
    const mlabel = MOD[step.modality] || "";
    $("sv-step-head").innerHTML =
      `<div class="sv-step-tag">第 ${idx + 1} 步${mlabel ? " · " + mlabel : ""}</div>` +
      `<h3 class="sv-step-h">${MMRender.escapeHtml(step.title || "")}</h3>` +
      `<div class="sv-step-task panel-md" id="sv-step-task"></div>`;
    MMRender.renderMarkdown($("sv-step-task"), step.prompt || "");

    // 清空可视对话（后端会话仍保留前几步上下文，模型看得到）
    $("sv-chat").innerHTML = "";
    setComposerEnabled(false);

    try {
      const r = await fetch("/api/solve/enter", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: problem.id, step_id: step.id,
          conversation_id: stepConvs[step.id] || "",
        }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        addError("进入这一步失败：" + (e.detail || r.status));
        return;
      }
      const d = await r.json();
      problemConvId = d.conversation_id;
      stepConvs[step.id] = problemConvId;   // 记下本步会话，恢复时接得上
      saveArchive();
      setComposerEnabled(true);
      // 自动以开场白起步（作为学生的第一句，请助教把本步问题理清楚）
      await sendChat(d.kickoff || "我们开始这一步吧。");
    } catch (e) {
      addError("进入这一步出错：" + e);
    }
  }

  // ========== 对话（/api/solve/chat，与 /api/chat 同协议） ==========
  function bindComposer() {
    const input = $("sv-input");
    const send = $("sv-send");
    input.addEventListener("input", () => {
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 160) + "px";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        const v = input.value.trim();
        if (v) { input.value = ""; input.style.height = "auto"; sendChat(v); }
      }
    });
    send.addEventListener("click", () => {
      if (streaming) { if (abort) abort.abort(); return; }
      const v = input.value.trim();
      if (v) { input.value = ""; input.style.height = "auto"; sendChat(v); }
    });
  }

  function setComposerEnabled(on) {
    $("sv-input").disabled = !on;
    $("sv-send").disabled = !on;
    if (on) $("sv-input").focus();
  }

  function setStreaming(on) {
    streaming = on;
    const send = $("sv-send");
    send.textContent = on ? "■" : "➤";
    send.title = on ? "停止生成" : "发送";
    send.classList.toggle("stop", on);
  }

  async function sendChat(text) {
    if (streaming) return;
    addUserMsg(text);
    setStreaming(true);
    svShowThinking();
    let bubble = null, buf = "";
    abort = new AbortController();
    try {
      const resp = await fetch("/api/solve/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ conversation_id: problemConvId, message: text }),
        signal: abort.signal,
      });
      if (!resp.ok) {
        let detail = "请求失败";
        try { detail = (await resp.json()).detail || detail; } catch { /* 非 JSON */ }
        svRemoveThinking();
        addError(detail);
        return;
      }
      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let sseBuf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        sseBuf += decoder.decode(value, { stream: true });
        const parts = sseBuf.split("\n\n");
        sseBuf = parts.pop();
        for (const part of parts) {
          const line = part.replace(/^data: /, "").trim();
          if (!line) continue;
          let ev; try { ev = JSON.parse(line); } catch { continue; }
          switch (ev.type) {
            case "meta":
              problemConvId = ev.conversation_id;
              break;
            case "token":
              if (!bubble) bubble = addAssistantShell();
              buf += ev.text;
              bubble.classList.add("cursor");
              MMRender.renderMarkdown(bubble, buf);
              scrollDown();
              break;
            case "tool_call":
              svRemoveThinking();
              if (bubble) { bubble.classList.remove("cursor"); bubble = null; buf = ""; }
              break;
            case "tool_result":
              renderToolCard(ev.display);
              scrollDown();
              break;
            case "done":
              if (!bubble && ev.content) bubble = addAssistantShell();
              if (bubble) { bubble.classList.remove("cursor"); MMRender.renderMarkdown(bubble, ev.content); }
              break;
            case "error":
              addError(ev.message || "出错了");
              break;
          }
        }
      }
    } catch (e) {
      if (!(e && e.name === "AbortError")) addError("连接出错：" + e);
    } finally {
      setStreaming(false);
      abort = null;
      svRemoveThinking();
      if (bubble) bubble.classList.remove("cursor");
    }
  }

  // ── 对话 DOM ──
  function addUserMsg(text) {
    const div = document.createElement("div");
    div.className = "sv-msg user anim-bubble";
    div.innerHTML = `<div class="sv-msg-role">我</div><div class="sv-bubble"></div>`;
    div.querySelector(".sv-bubble").textContent = text;
    $("sv-chat").appendChild(div);
    scrollDown();
  }

  function addAssistantShell() {
    svRemoveThinking();
    const div = document.createElement("div");
    div.className = "sv-msg assistant anim-bubble";
    div.innerHTML = `<div class="sv-msg-role">助教</div><div class="sv-bubble cursor"></div>`;
    $("sv-chat").appendChild(div);
    scrollDown();
    return div.querySelector(".sv-bubble");
  }

  // AI 思考三点占位
  let svThinking = null;
  function svShowThinking() {
    svRemoveThinking();
    if (!window.MMAnim) return;
    svThinking = document.createElement("div");
    svThinking.className = "sv-msg assistant";
    svThinking.innerHTML = `<div class="sv-msg-role">助教</div>`;
    svThinking.appendChild(MMAnim.thinkingEl());
    $("sv-chat").appendChild(svThinking);
    scrollDown();
  }
  function svRemoveThinking() {
    if (svThinking) { svThinking.remove(); svThinking = null; }
  }

  function addError(msg) {
    svRemoveThinking();
    const div = document.createElement("div");
    div.className = "sv-msg assistant";
    div.innerHTML = `<div class="sv-msg-role">助教</div><div class="sv-bubble"><p style="color:var(--rouge)">⚠ ${MMRender.escapeHtml(msg)}</p></div>`;
    $("sv-chat").appendChild(div);
    scrollDown();
  }

  function scrollDown() { const c = $("sv-chat"); c.scrollTop = c.scrollHeight; }

  // ── 工具卡片（紧凑版） ──
  function renderToolCard(display) {
    if (!display) return;
    const card = document.createElement("div");
    card.className = "tool-card";
    const esc = MMRender.escapeHtml;
    let label = "工具", cls = "ok", bodyHtml = "", open = false;

    if (display.type === "search") {
      if (display.out_of_scope) {
        cls = "err"; label = `检索「${esc(display.query)}」· 超出知识库范围`;
        bodyHtml = `<div class="out-scope">知识库中没有充分相关的内容，助教将谨慎补充。</div>`;
      } else {
        label = `检索知识库「${esc(display.query)}」· ${(display.citations || []).length} 个出处`;
        const chips = (display.citations || []).map((c) =>
          `<span class="cite"><span class="cid">${esc(String(c.chunk_id))}</span>${esc(c.title || "")}<span class="score">${esc(String(c.score))}</span></span>`
        ).join("");
        bodyHtml = `<div class="cite-list">${chips}</div>`;
      }
    } else if (display.type === "code_run") {
      cls = display.success ? "ok" : "err";
      const status = display.timed_out ? "超时" : display.success ? "成功" : "出错";
      label = `运行 Python 代码 · ${status}`; open = true;
      bodyHtml = `<pre><code class="language-python">${esc(display.code || "")}</code></pre>`;
      if (display.stdout) bodyHtml += `<div class="run-out">${esc(display.stdout)}</div>`;
      if (display.stderr) bodyHtml += `<div class="run-out stderr">${esc(display.stderr)}</div>`;
      for (const img of display.images || []) {
        bodyHtml += `<img class="run-img" src="data:image/png;base64,${img.data_base64}" alt="${esc(img.name || "")}" />`;
      }
    } else if (display.type === "document") {
      if (display.error) { cls = "err"; label = `读取文档「${esc(display.filename)}」· 失败`; bodyHtml = `<div class="run-out stderr">${esc(display.error)}</div>`; }
      else { label = `读取文档「${esc(display.filename)}」· ${display.chars} 字符`; bodyHtml = `<div class="out-scope">已读取文档内容并交给助教分析。</div>`; }
    } else if (display.type === "file_op") {
      const verbs = { write: "写入文件", read: "读取文件", list: "列出目录", delete: "删除文件" };
      cls = display.ok ? "ok" : "err";
      label = `${verbs[display.action] || "文件操作"}「${esc(display.path || "")}」· ${display.ok ? "成功" : "失败"}`;
      bodyHtml = display.ok ? `<div class="out-scope">${esc(display.action || "")} 完成。</div>` : `<div class="run-out stderr">${esc(display.error || "操作失败")}</div>`;
      // AI 写文件成功 → 通知中栏编辑器载入（由 editor-preview.js 监听）
      if (display.action === "write" && display.ok && display.path) {
        window.dispatchEvent(new CustomEvent("ai-file-write", { detail: { path: display.path } }));
      }
    } else {
      label = "工具：" + esc(display.type || "");
    }

    card.innerHTML =
      `<div class="tool-inner${open ? " open" : ""}">
        <div class="tool-head ${cls}"><span class="dot"></span><span class="label">${label}</span><span class="toggle">▸</span></div>
        <div class="tool-body">${bodyHtml}</div>
      </div>`;
    const inner = card.querySelector(".tool-inner");
    inner.querySelector(".tool-head").onclick = () => inner.classList.toggle("open");
    card.querySelectorAll("pre code").forEach((b) => { if (window.hljs) hljs.highlightElement(b); });
    $("sv-chat").appendChild(card);
  }

  // ========== 模型与 API 设置（与工作台同款，独立实现） ==========
  const PROVIDER_PRESETS = {
    gateway:  { base_url: "https://math-modeling.top/v1", model: "deepseek-v4-pro" },
    deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    qwen:     { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
    openai:   { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    moonshot: { base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
    custom:   { base_url: "", model: "" },
  };

  async function loadSettings() {
    try {
      const s = await (await fetch("/api/settings")).json();
      $("setting-api-key").value = s.api_key || "";
      $("setting-base-url").value = s.base_url || "";
      $("setting-model").value = s.model || "";
      $("model-badge").textContent = s.model || "";
      const matched = Object.entries(PROVIDER_PRESETS).find(([_, p]) => p.base_url === s.base_url && p.model === s.model);
      markPreset(matched ? matched[0] : "custom");
    } catch { /* 忽略 */ }
  }

  function markPreset(provider) {
    document.querySelectorAll("#provider-presets .preset").forEach((b) => {
      b.classList.toggle("active", b.dataset.provider === provider);
    });
    if (provider === "custom") return;
    const p = PROVIDER_PRESETS[provider];
    if (p && p.base_url) $("setting-base-url").value = p.base_url;
    if (p && p.model) $("setting-model").value = p.model;
  }

  function setupSettings() {
    document.querySelectorAll("#provider-presets .preset").forEach((btn) => {
      btn.addEventListener("click", () => {
        const provider = btn.dataset.provider;
        markPreset(provider);
        if (provider !== "custom") {
          const p = PROVIDER_PRESETS[provider];
          $("setting-base-url").value = p.base_url || "";
          $("setting-model").value = p.model || "";
        }
      });
    });
    $("btn-settings").onclick = () => { $("settings-overlay").classList.add("open"); loadSettings(); };
    $("btn-close-settings").onclick = () => $("settings-overlay").classList.remove("open");
    $("settings-overlay").addEventListener("click", (e) => {
      if (e.target === $("settings-overlay")) $("settings-overlay").classList.remove("open");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { $("settings-overlay").classList.remove("open"); $("model-menu").hidden = true; }
    });
    $("btn-save-settings").onclick = async () => {
      const statusEl = $("settings-status");
      statusEl.className = "settings-status";

      const apiKey = $("setting-api-key").value.trim();
      if (apiKey && apiKey.includes("****")) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "请填入真实的 API Key（当前为掩码占位符，未实际修改）";
        $("setting-api-key").focus();
        return;
      }
      if (apiKey && apiKey.length < 15) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "API Key 太短，请检查是否完整粘贴";
        $("setting-api-key").focus();
        return;
      }

      statusEl.textContent = "保存中…";
      try {
        const resp = await fetch("/api/settings", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_key: $("setting-api-key").value,
            base_url: $("setting-base-url").value,
            model: $("setting-model").value,
          }),
        });
        if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.detail || resp.statusText); }
        const result = await resp.json();
        $("model-badge").textContent = result.model || "";
        statusEl.className = "settings-status ok";
        statusEl.textContent = "设置已保存，模型已切换";
        setTimeout(() => { $("settings-overlay").classList.remove("open"); statusEl.textContent = ""; }, 1200);
      } catch (e) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "保存失败：" + e.message;
      }
    };
  }

  function setupModelMenu() {
    const pill = $("model-pill");
    const menu = $("model-menu");
    const selector = $("model-selector");
    if (!pill || !menu || !selector) return;
    const closeMenu = () => { menu.hidden = true; };

    function markActive() {
      const cur = ($("model-badge").textContent || "").trim();
      menu.querySelectorAll(".model-opt[data-provider]").forEach((b) => {
        const p = PROVIDER_PRESETS[b.dataset.provider];
        b.classList.toggle("active", !!p && p.model === cur);
      });
    }

    pill.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      if (!menu.hidden) markActive();
    });

    menu.querySelectorAll(".model-opt[data-provider]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const p = PROVIDER_PRESETS[btn.dataset.provider];
        if (!p) return;
        closeMenu();
        const badge = $("model-badge");
        const prev = badge.textContent;
        badge.textContent = p.model;
        $("setting-base-url").value = p.base_url;
        $("setting-model").value = p.model;
        markPreset(btn.dataset.provider);
        try {
          const resp = await fetch("/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ base_url: p.base_url, model: p.model }),
          });
          if (!resp.ok) throw new Error();
          const r = await resp.json();
          badge.textContent = r.model || p.model;
        } catch {
          badge.textContent = prev;
          alert("切换模型失败，请在设置里检查 API 配置。");
        }
      });
    });

    const more = $("model-menu-more");
    if (more) more.addEventListener("click", () => { closeMenu(); $("settings-overlay").classList.add("open"); loadSettings(); });
    document.addEventListener("click", (e) => { if (!selector.contains(e.target)) closeMenu(); });
  }

  // ===== 首次使用 · 工作目录选择弹窗 =====
  (function initFolderModal() {
    var STORAGE_KEY = "mm_folder_setup_done";
    if (localStorage.getItem(STORAGE_KEY) === "1") return;

    var overlay = document.getElementById("fm-overlay");
    if (!overlay) return;
    overlay.hidden = false;

    // Tab 切换
    var tabs = overlay.querySelectorAll(".fm-tab");
    var panelNew = document.getElementById("fm-panel-new");
    var panelExisting = document.getElementById("fm-panel-existing");
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () {
        tabs.forEach(function (t) { t.classList.remove("active"); });
        tab.classList.add("active");
        if (tab.dataset.tab === "new") {
          panelNew.hidden = false;
          panelExisting.hidden = true;
        } else {
          panelNew.hidden = true;
          panelExisting.hidden = false;
        }
      });
    });

    // 完成：设置工作目录，关闭弹窗
    function finish(dir) {
      var wd = document.getElementById("workdir");
      if (wd) { wd.value = dir; }
      // 也通过 IDE 模块设置
      if (window.IDE && window.IDE.setWorkdir) { window.IDE.setWorkdir(dir); }
      localStorage.setItem(STORAGE_KEY, "1");
      overlay.hidden = true;
    }

    // 新建文件夹
    document.getElementById("fm-btn-create").addEventListener("click", function () {
      var name = document.getElementById("fm-new-name").value.trim();
      var parent = document.getElementById("fm-new-parent").value.trim();
      var msgEl = document.getElementById("fm-msg-new");

      if (!name) { msgEl.textContent = "请输入文件夹名称"; return; }

      if (!parent) {
        var home = "";
        try { home = process && process.env ? (process.env.USERPROFILE || process.env.HOME || "") : ""; } catch (_) {}
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
          if (data.ok) { finish(fullPath); }
          else { msgEl.textContent = "创建失败：" + (data.error || "未知错误"); }
        })
        .catch(function (e) { msgEl.textContent = "请求失败：" + e.message; });
    });

    // 打开已有文件夹
    document.getElementById("fm-btn-open").addEventListener("click", function () {
      var path = document.getElementById("fm-existing-path").value.trim();
      var msgEl = document.getElementById("fm-msg-existing");

      if (!path) { msgEl.textContent = "请输入文件夹路径"; return; }

      fetch("/api/ide/tree?path=" + encodeURIComponent(path))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          if (data.ok) { finish(path); }
          else { msgEl.textContent = "无法访问该目录：" + (data.error || "请检查路径是否正确"); }
        })
        .catch(function (e) { msgEl.textContent = "请求失败：" + e.message; });
    });

    // 回车快捷提交
    document.getElementById("fm-new-name").addEventListener("keydown", function (e) {
      if (e.key === "Enter") document.getElementById("fm-btn-create").click();
    });
    document.getElementById("fm-existing-path").addEventListener("keydown", function (e) {
      if (e.key === "Enter") document.getElementById("fm-btn-open").click();
    });

    // 跳过
    document.getElementById("fm-skip").addEventListener("click", function () {
      localStorage.setItem(STORAGE_KEY, "1");
      overlay.hidden = true;
    });
  })();
})();
