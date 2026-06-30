// ===== 数学建模助教 前端逻辑 =====

const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const inputEl = $("#input");
const sendBtn = $("#btn-send");
const convListEl = $("#conv-list");

let currentConvId = null;
let streaming = false;
let attachedFiles = [];  // 已上传到当前会话工作目录的文件名

// ---------- Markdown + 公式渲染 ----------
marked.setOptions({
  breaks: true,
  highlight(code, lang) {
    try {
      if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
      return hljs.highlightAuto(code).value;
    } catch { return code; }
  },
});

// 把代码块和数学公式先从原文里「保护」出来，再交给 marked；
// 原因：marked 会把 \(…\) \[…\] 当成转义字符吞掉反斜杠，导致 KaTeX 找不到定界符；
// 公式里的 | _ * 等也会破坏表格 / 触发斜体。先抽出占位、解析后再安全地还原（占位符用私有区字符  / ）。
function preprocessMath(src) {
  const codes = [];
  const maths = [];
  let s = src || "";

  // 1) 先抽出围栏代码块与行内代码，避免里面的 $ / \( 被当成公式
  s = s.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`/g, (m) => {
    codes.push(m);
    return `c${codes.length - 1}`;
  });

  // 2) 抽出公式（块级在前、行内在后）
  const stash = (tex, display) => {
    maths.push({ tex, display });
    return `m${maths.length - 1}`;
  };
  s = s
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
    .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => stash(t, false))
    .replace(/\$(?!\s)([^\n$]+?)(?<!\s)\$/g, (_, t) => stash(t, false));

  // 3) 代码块原样还原，交给 marked 正常渲染
  s = s.replace(/c(\d+)/g, (_, i) => codes[+i]);
  return { text: s, maths };
}

function restoreMath(html, maths) {
  return html.replace(/m(\d+)/g, (_, i) => {
    const m = maths[+i];
    if (!m) return "";
    const safe = escapeHtml(m.tex);
    return m.display ? `$$${safe}$$` : `$${safe}$`;
  });
}

function renderMarkdown(el, text) {
  const { text: pre, maths } = preprocessMath(text);
  el.innerHTML = restoreMath(marked.parse(pre), maths);
  el.querySelectorAll("pre code").forEach((b) => {
    if (!b.classList.contains("hljs")) hljs.highlightElement(b);
  });
  addCopyButtons(el);
  if (window.renderMathInElement) {
    renderMathInElement(el, {
      delimiters: [
        { left: "$$", right: "$$", display: true },
        { left: "$", right: "$", display: false },
        { left: "\\[", right: "\\]", display: true },
        { left: "\\(", right: "\\)", display: false },
      ],
      throwOnError: false,
    });
  }
}

// 给每个代码块加「复制」按钮
function addCopyButtons(el) {
  el.querySelectorAll("pre").forEach((pre) => {
    if (pre.querySelector(".copy-btn")) return;
    const btn = document.createElement("button");
    btn.className = "copy-btn";
    btn.textContent = "复制";
    btn.onclick = () => {
      const code = pre.querySelector("code");
      navigator.clipboard.writeText(code ? code.innerText : pre.innerText).then(() => {
        btn.textContent = "✓ 已复制";
        btn.classList.add("anim-pop");
        setTimeout(() => { btn.textContent = "复制"; btn.classList.remove("anim-pop"); }, 1500);
      });
    };
    pre.appendChild(btn);
  });
}

// ---------- 消息 DOM ----------
function clearWelcome() {
  const w = $("#welcome");
  if (w) w.remove();
}

function addUserMsg(text, animate) {
  clearWelcome();
  const div = document.createElement("div");
  div.className = "msg user" + (animate ? " anim-bubble" : "");
  div.innerHTML = `<div class="msg-role">我</div><div class="bubble"></div>`;
  div.querySelector(".bubble").textContent = text;
  messagesEl.appendChild(div);
  scrollDown();
  return div;
}

// 给某条用户消息挂「↶ 回到这一步」按钮（回溯对话 + 撤销其后 AI 对文件的改动）
function addRollbackButton(userDiv, index) {
  if (!userDiv || userDiv.querySelector(".rollback-btn")) return;
  const btn = document.createElement("button");
  btn.className = "rollback-btn";
  btn.textContent = "↶ 回到这一步";
  btn.title = "回到这条消息之前，并撤销之后 AI 对文件的改动";
  btn.onclick = () => rollbackTo(index);
  userDiv.appendChild(btn);
}

async function rollbackTo(index) {
  if (streaming || !currentConvId) return;
  if (!confirm("将回到这一步，并撤销之后 AI 对文件的改动（仅对 AI 写/删过的文件）。确定？")) return;
  try {
    const resp = await fetch(`/api/conversations/${currentConvId}/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ index }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert("回溯失败：" + (err.detail || resp.status));
      return;
    }
    const data = await resp.json();
    await openConversation(currentConvId);
    // 预填被回退的用户消息，便于改写后重发
    if (data.message) {
      inputEl.value = data.message;
      autoGrow();
      inputEl.focus();
    }
  } catch (e) {
    alert("回溯出错：" + e);
  }
}

function addAssistantShell(animate) {
  removeThinking();
  const div = document.createElement("div");
  div.className = "msg assistant" + (animate ? " anim-bubble" : "");
  div.innerHTML = `<div class="msg-role">助教</div><div class="bubble cursor"></div>`;
  messagesEl.appendChild(div);
  scrollDown();
  return div.querySelector(".bubble");
}

// AI 思考占位（首个 token / 工具事件到达前显示三点动画）
let thinkingRow = null;
function showThinking() {
  removeThinking();
  if (!window.MMAnim) return;
  thinkingRow = document.createElement("div");
  thinkingRow.className = "msg assistant thinking-row";
  thinkingRow.innerHTML = `<div class="msg-role">助教</div>`;
  thinkingRow.appendChild(MMAnim.thinkingEl());
  messagesEl.appendChild(thinkingRow);
  scrollDown();
}
function removeThinking() {
  if (thinkingRow) { thinkingRow.remove(); thinkingRow = null; }
}

// 工具事件卡片
function addToolCard(kind) {
  const card = document.createElement("div");
  card.className = "tool-card";
  card.innerHTML = `
    <div class="tool-inner">
      <div class="tool-head"><span class="dot"></span><span class="label"></span><span class="toggle">▸</span></div>
      <div class="tool-body"></div>
    </div>`;
  messagesEl.appendChild(card);
  const inner = card.querySelector(".tool-inner");
  inner.querySelector(".tool-head").onclick = () => inner.classList.toggle("open");
  scrollDown();
  return card;
}

function renderSearchCard(card, display) {
  const head = card.querySelector(".tool-head");
  const body = card.querySelector(".tool-body");
  if (display.out_of_scope) {
    head.classList.add("err");
    head.querySelector(".label").textContent = `检索「${display.query}」· 超出知识库范围`;
    body.innerHTML = `<div class="out-scope">知识库中没有充分相关的内容，助教将谨慎补充并给出学习建议。</div>`;
  } else {
    head.classList.add("ok");
    head.querySelector(".label").textContent = `检索知识库「${display.query}」· ${display.citations.length} 个出处`;
    const chips = display.citations
      .map((c) => {
        const diff = c.difficulty ? `<span class="diff">${escapeHtml(c.difficulty)}</span>` : "";
        return `<span class="cite"><span class="cid">${c.chunk_id}</span>${escapeHtml(c.title)}${diff}<span class="score">${c.score}</span></span>`;
      })
      .join("");
    body.innerHTML = `<div class="cite-list">${chips}</div>`;
  }
}

function renderCodeCard(card, display) {
  const head = card.querySelector(".tool-head");
  const body = card.querySelector(".tool-body");
  head.classList.add(display.success ? "ok" : "err");
  const status = display.timed_out ? "超时" : display.success ? "成功" : "出错";
  head.querySelector(".label").textContent = `运行 Python 代码 · ${status}`;

  let html = `<pre><code class="language-python">${escapeHtml(display.code)}</code></pre>`;
  if (display.stdout) html += `<div class="run-out">${escapeHtml(display.stdout)}</div>`;
  if (display.stderr) html += `<div class="run-out stderr">${escapeHtml(display.stderr)}</div>`;
  for (const img of display.images || []) {
    html += `<img class="run-img" src="data:image/png;base64,${img.data_base64}" alt="${img.name}" />`;
  }
  body.innerHTML = html;
  body.querySelectorAll("pre code").forEach((b) => hljs.highlightElement(b));
  card.querySelector(".tool-inner").classList.add("open"); // 代码默认展开
}

function renderDocumentCard(card, display) {
  const head = card.querySelector(".tool-head");
  const body = card.querySelector(".tool-body");
  if (display.error) {
    head.classList.add("err");
    head.querySelector(".label").textContent = `读取文档「${display.filename}」· 失败`;
    body.innerHTML = `<div class="run-out stderr">${escapeHtml(display.error)}</div>`;
  } else {
    head.classList.add("ok");
    head.querySelector(".label").textContent = `读取文档「${display.filename}」· ${display.chars} 字符`;
    body.innerHTML = `<div class="out-scope">已读取文档内容并交给助教分析。</div>`;
  }
}

function renderFileOpCard(card, display) {
  const head = card.querySelector(".tool-head");
  const body = card.querySelector(".tool-body");
  const verbs = { write: "写入文件", read: "读取文件", list: "列出目录", delete: "删除文件" };
  const verb = verbs[display.action] || "文件操作";
  head.classList.add(display.ok ? "ok" : "err");
  head.querySelector(".label").textContent = `${verb}「${display.path}」· ${display.ok ? "成功" : "失败"}`;

  if (!display.ok) {
    body.innerHTML = `<div class="run-out stderr">${escapeHtml(display.error || "操作失败")}</div>`;
    return;
  }
  let summary = "";
  if (display.action === "write") summary = `${display.overwritten ? "覆盖写入" : "已创建"}· ${display.bytes} 字节`;
  else if (display.action === "read") summary = `已读取 ${display.chars} 字符`;
  else if (display.action === "list") summary = `${display.count} 项${display.truncated ? "（已截断）" : ""}`;
  else if (display.action === "delete") summary = "已删除";
  let html = `<div class="out-scope">${escapeHtml(summary)}</div>`;
  if (display.action === "list" && Array.isArray(display.items)) {
    const rows = display.items
      .map((it) => `${it.is_dir ? "📁" : "📄"} ${escapeHtml(it.name)}${it.is_dir ? "/" : ""}`)
      .join("<br/>");
    html += `<div class="run-out">${rows}</div>`;
  }
  body.innerHTML = html;
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function scrollDown() { messagesEl.scrollTop = messagesEl.scrollHeight; }

// ---------- 发送 + SSE 流 ----------
let abortController = null;

function setStreamingUI(on) {
  streaming = on;
  if (on) {
    sendBtn.textContent = "■";
    sendBtn.title = "停止生成";
    sendBtn.classList.add("stop");
  } else {
    sendBtn.textContent = "➤";
    sendBtn.title = "发送";
    sendBtn.classList.remove("stop");
  }
}

function stopStreaming() {
  if (abortController) abortController.abort();
}

async function send(text, opts = {}) {
  const regenerate = !!opts.regenerate;
  if (streaming) { stopStreaming(); return; }
  if (!regenerate && !text.trim()) return;

  setStreamingUI(true);
  if (!regenerate) {
    addUserMsg(text, true);
    inputEl.value = "";
    autoGrow();
  }
  attachedFiles = [];
  renderAttachments();
  showThinking();

  let bubble = null;
  let buf = "";
  abortController = new AbortController();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: currentConvId, message: text || "", regenerate }),
      signal: abortController.signal,
    });

    if (!resp.ok) {
      let detail = "请求失败";
      try { detail = (await resp.json()).detail || detail; } catch { /* 非 JSON */ }
      if (!bubble) bubble = addAssistantShell();
      bubble.classList.remove("cursor");
      bubble.innerHTML = `<p style="color:var(--rouge)">${escapeHtml(detail)}</p>`;
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
        let ev;
        try { ev = JSON.parse(line); } catch { continue; }
        handleEvent(ev);
      }
    }
  } catch (e) {
    if (e.name === "AbortError") {
      // 用户主动停止：保留已生成内容
      if (bubble) bubble.classList.remove("cursor");
    } else {
      if (!bubble) bubble = addAssistantShell();
      bubble.classList.remove("cursor");
      bubble.innerHTML = `<p style="color:var(--rouge)">连接出错：${escapeHtml(String(e))}</p>`;
    }
  } finally {
    setStreamingUI(false);
    abortController = null;
    removeThinking();
    if (bubble) bubble.classList.remove("cursor");
    // 从已保存的会话重渲染：下标稳定，顺带给每条用户消息挂上回溯按钮
    if (currentConvId) await openConversation(currentConvId);
    else { addRegenerateButton(); loadConversations(); }
  }

  function handleEvent(ev) {
    switch (ev.type) {
      case "meta":
        currentConvId = ev.conversation_id;
        $("#topbar-title").textContent = ev.title || "新对话";
        break;
      case "token":
        if (!bubble) bubble = addAssistantShell(true);
        buf += ev.text;
        bubble.classList.add("cursor");
        renderMarkdown(bubble, buf);
        scrollDown();
        break;
      case "tool_call":
        // 标记：后面 tool_result 会渲染。先收尾当前气泡
        removeThinking();
        if (bubble) { bubble.classList.remove("cursor"); bubble = null; buf = ""; }
        break;
      case "tool_result": {
        const d = ev.display;
        const card = addToolCard(d.type);
        if (d.type === "search") renderSearchCard(card, d);
        else if (d.type === "code_run") renderCodeCard(card, d);
        else if (d.type === "document") renderDocumentCard(card, d);
        else if (d.type === "file_op") renderFileOpCard(card, d);
        // AI 写文件成功 → 通知工作台把该文件实时加载进编辑器（纯聊天页无监听者，无副作用）
        if (d.type === "file_op" && d.action === "write" && d.ok && d.path) {
          window.dispatchEvent(new CustomEvent("ai-file-write", { detail: { path: d.path } }));
        }
        scrollDown();
        break;
      }
      case "done":
        if (!bubble && ev.content) bubble = addAssistantShell();
        if (bubble) {
          bubble.classList.remove("cursor");
          renderMarkdown(bubble, ev.content);
        }
        break;
      case "saved":
        loadConversations();
        break;
      case "error":
        if (!bubble) bubble = addAssistantShell();
        bubble.classList.remove("cursor");
        bubble.innerHTML += `<p style="color:var(--rouge)">⚠ ${escapeHtml(ev.message)}</p>`;
        break;
    }
  }
}

// 在最后一条助教消息后放「重新生成」按钮（仅保留最新一个）
function addRegenerateButton() {
  document.querySelectorAll(".regen-row").forEach((r) => r.remove());
  const last = messagesEl.querySelector(".msg.assistant:last-of-type");
  if (!last) return;
  const row = document.createElement("div");
  row.className = "regen-row";
  const btn = document.createElement("button");
  btn.className = "regen-btn";
  btn.textContent = "↻ 重新生成";
  btn.onclick = () => regenerate();
  row.appendChild(btn);
  last.appendChild(row);
}

function regenerate() {
  if (streaming || !currentConvId) return;
  // 移除当前最后一条助教消息及其工具卡片，再请求重新生成
  document.querySelectorAll(".regen-row").forEach((r) => r.remove());
  const last = messagesEl.querySelector(".msg.assistant:last-of-type");
  if (last) {
    // 一并移除该助教消息前面紧邻的工具卡片
    let node = last.previousElementSibling;
    last.remove();
    while (node && node.classList.contains("tool-card")) {
      const prev = node.previousElementSibling;
      node.remove();
      node = prev;
    }
  }
  send("", { regenerate: true });
}

// ---------- 会话列表 ----------
async function loadConversations() {
  try {
    const list = await (await fetch("/api/conversations")).json();
    convListEl.innerHTML = "";
    for (const c of list) {
      const item = document.createElement("div");
      item.className = "conv-item" + (c.id === currentConvId ? " active" : "");
      item.innerHTML = `<span>${escapeHtml(c.title)}</span><span class="conv-del" title="删除">×</span>`;
      item.querySelector("span").onclick = () => openConversation(c.id);
      item.firstChild.onclick = () => openConversation(c.id);
      item.querySelector(".conv-del").onclick = (e) => { e.stopPropagation(); deleteConversation(c.id); };
      convListEl.appendChild(item);
    }
  } catch {}
}

async function openConversation(id) {
  if (streaming) stopStreaming();
  const conv = await (await fetch(`/api/conversations/${id}`)).json();
  currentConvId = id;
  attachedFiles = [];
  renderAttachments();
  $("#topbar-title").textContent = conv.title;
  messagesEl.innerHTML = "";
  for (let i = 0; i < conv.messages.length; i++) {
    const m = conv.messages[i];
    if (m.role === "user") {
      addRollbackButton(addUserMsg(m.content), i);
    } else if (m.role === "assistant") {
      // 回放工具事件
      for (const ev of m.events || []) {
        if (ev.type === "tool_result") {
          const d = ev.display;
          const card = addToolCard(d.type);
          if (d.type === "search") renderSearchCard(card, d);
          else if (d.type === "code_run") renderCodeCard(card, d);
          else if (d.type === "document") renderDocumentCard(card, d);
          else if (d.type === "file_op") renderFileOpCard(card, d);
        }
      }
      const bubble = addAssistantShell();
      bubble.classList.remove("cursor");
      renderMarkdown(bubble, m.content);
    }
    // tool 角色消息跳过，仅供后端恢复上下文
  }
  addRegenerateButton();
  loadConversations();
  scrollDown();
}

async function deleteConversation(id) {
  await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (id === currentConvId) newConversation();
  loadConversations();
}

function newConversation() {
  if (streaming) stopStreaming();
  currentConvId = null;
  attachedFiles = [];
  renderAttachments();
  $("#topbar-title").textContent = "新对话";
  messagesEl.innerHTML = `
    <div class="welcome" id="welcome">
      <div class="vertical-deco">数学建模助教</div>
      <h1>数学建模助教</h1>
      <p>基于 40 小时课程知识库，帮你拆解问题、有出处地解答、写并自测 Python 代码、手把手带你配环境与排版。</p>
      <div class="suggestions">
        <button class="chip">层次分析法怎么构造判断矩阵？一致性检验怎么做？</button>
        <button class="chip">帮我用 Python 解一个线性规划并画出可行域</button>
        <button class="chip">我是零基础，怎么在 Windows 上安装 Python？</button>
        <button class="chip">数学建模论文的摘要该怎么写？</button>
      </div>
    </div>`;
  bindChips();
  enhanceWelcome();
  loadConversations();
}

// ---------- 健康检查 ----------
async function loadHealth() {
  try {
    const h = await (await fetch("/api/health")).json();
    const total = h.knowledge_units || 0;
    $("#kb-status").innerHTML = h.ok
      ? `知识库：${total} 个知识点已就绪`
      : `⚠ ${escapeHtml((h.problems || []).join("；"))}`;
  } catch {
    $("#kb-status").textContent = "后端未连接";
  }
}

// ---------- 文件上传 ----------
function renderAttachments() {
  const box = $("#attachments");
  if (!box) return;
  box.innerHTML = attachedFiles
    .map((f) => `<span class="attach-chip" data-name="${escapeHtml(f)}">📄 ${escapeHtml(f)} <span class="attach-del">×</span></span>`)
    .join("");
  box.querySelectorAll(".attach-del").forEach((el) => {
    el.onclick = () => {
      const name = el.parentElement.dataset.name;
      attachedFiles = attachedFiles.filter((x) => x !== name);
      renderAttachments();
    };
  });
}

async function uploadFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  if (currentConvId) fd.append("conversation_id", currentConvId);
  try {
    const resp = await fetch("/api/upload", { method: "POST", body: fd });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      alert("上传失败：" + (err.detail || resp.status));
      return;
    }
    const data = await resp.json();
    currentConvId = data.conversation_id;
    if (!attachedFiles.includes(data.filename)) attachedFiles.push(data.filename);
    renderAttachments();
  } catch (e) {
    alert("上传出错：" + e);
  }
}

// ---------- 导出对话 ----------
async function exportConversation() {
  if (!currentConvId) { alert("当前没有可导出的对话。"); return; }
  const conv = await (await fetch(`/api/conversations/${currentConvId}`)).json();
  const lines = [`# ${conv.title || "对话"}`, ""];
  for (const m of conv.messages) {
    lines.push(m.role === "user" ? "## 🧑 我" : "## 🎓 助教");
    for (const ev of m.events || []) {
      if (ev.type === "tool_result" && ev.display) {
        const d = ev.display;
        if (d.type === "search") lines.push(`> 🔎 检索「${d.query}」`);
        else if (d.type === "code_run") lines.push("```python\n" + (d.code || "") + "\n```");
        else if (d.type === "document") lines.push(`> 📄 读取文档「${d.filename}」`);
        else if (d.type === "file_op") lines.push(`> 🗂️ ${d.action} 「${d.path}」— ${d.ok ? "成功" : "失败"}`);
      }
    }
    lines.push(m.content || "", "");
  }
  const blob = new Blob([lines.join("\n")], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${(conv.title || "对话").replace(/[\\/:*?"<>|]/g, "_")}.md`;
  a.click();
  URL.revokeObjectURL(a.href);
}

// ---------- 输入框 ----------
function autoGrow() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 180) + "px";
}
inputEl.addEventListener("input", autoGrow);
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(inputEl.value); }
});
sendBtn.onclick = () => send(inputEl.value);
$("#btn-new").onclick = newConversation;
$("#btn-toggle").onclick = () => $("#sidebar").classList.toggle("collapsed");
$("#btn-export").onclick = exportConversation;
$("#btn-attach").onclick = () => $("#file-input").click();
// 引导按钮
const tourBtn = $("#btn-tour");
if (tourBtn) {
  tourBtn.onclick = () => {
    if (window.OnboardingTour) {
      OnboardingTour.reset();
      OnboardingTour.start();
    }
  };
}
$("#file-input").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (file) uploadFile(file);
  e.target.value = "";  // 允许重复选同一文件
});

// 建议气泡（每次重建 welcome 后需重新绑定）
function bindChips() {
  document.querySelectorAll(".chip").forEach((c) => {
    c.classList.add("press");
    c.onclick = () => send(c.textContent);
  });
}
bindChips();

// 欢迎页：建议卡级联入场 + 极淡点阵网络母题
function enhanceWelcome() {
  const w = $("#welcome");
  if (!w || !window.MMAnim) return;
  if (!w.querySelector(".mm-ambient")) {
    const cv = document.createElement("canvas");
    cv.className = "mm-ambient";
    w.insertBefore(cv, w.firstChild);
    MMAnim.ambientNetwork(cv, { density: 22000, inkAlpha: 0.07, dotAlpha: 0.28, interactive: false, minPoints: 14, maxPoints: 46 });
  }
  const sugg = w.querySelector(".suggestions");
  if (sugg) MMAnim.reveal(sugg, { observe: false });
}
enhanceWelcome();

// 初始化
loadHealth();
loadConversations();
loadSettings();

// 新用户引导流程：聊天页首次加载时自动触发
(function checkTour() {
  if (window.OnboardingTour && !OnboardingTour.isCompleted()) {
    setTimeout(() => {
      OnboardingTour.start();
    }, 600);
  }
})();

// ===== 模型与 API 设置 =====
const PROVIDER_PRESETS = {
  gateway:   { base_url: "https://math-modeling.top/v1", model: "deepseek-v4-pro" },
  deepseek:  { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
  qwen:      { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
  openai:    { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
  moonshot:  { base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
  custom:    { base_url: "", model: "" },
};

async function loadSettings() {
  try {
    const s = await (await fetch("/api/settings")).json();
    $("#setting-api-key").value = s.api_key || "";
    $("#setting-base-url").value = s.base_url || "";
    $("#setting-model").value = s.model || "";
    $("#model-badge").textContent = s.model || "";
    // 匹配预设
    const matched = Object.entries(PROVIDER_PRESETS).find(([_, p]) =>
      p.base_url === s.base_url && p.model === s.model
    );
    markPreset(matched ? matched[0] : "custom");
  } catch {}
}

function markPreset(provider) {
  document.querySelectorAll("#provider-presets .preset").forEach((b) => {
    b.classList.toggle("active", b.dataset.provider === provider);
  });
  // 自定义时清空 base_url 与 model 的占位，让用户自己填
  if (provider === "custom") {
    // 保持当前值不变
    return;
  }
  const p = PROVIDER_PRESETS[provider];
  if (p && p.base_url) $("#setting-base-url").value = p.base_url;
  if (p && p.model) $("#setting-model").value = p.model;
}

// 预设按钮点击
document.querySelectorAll("#provider-presets .preset").forEach((btn) => {
  btn.addEventListener("click", () => {
    const provider = btn.dataset.provider;
    markPreset(provider);
    if (provider !== "custom") {
      const p = PROVIDER_PRESETS[provider];
      $("#setting-base-url").value = p.base_url || "";
      $("#setting-model").value = p.model || "";
    }
  });
});

// 打开 / 关闭弹窗
$("#btn-settings").onclick = () => {
  $("#settings-overlay").classList.add("open");
  loadSettings();
};
$("#btn-close-settings").onclick = () => $("#settings-overlay").classList.remove("open");
$("#settings-overlay").addEventListener("click", (e) => {
  if (e.target === $("#settings-overlay")) $("#settings-overlay").classList.remove("open");
});
// Esc 关闭设置弹窗
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("#settings-overlay").classList.remove("open");
});

// 保存设置
$("#btn-save-settings").onclick = async () => {
  const statusEl = $("#settings-status");
  statusEl.className = "settings-status";

  const apiKey = $("#setting-api-key").value.trim();
  // 前端校验：拒绝掩码 key（打开设置面板后未修改就保存）
  if (apiKey && apiKey.includes("****")) {
    statusEl.className = "settings-status err";
    statusEl.textContent = "请填入真实的 API Key（当前为掩码占位符，未实际修改）";
    $("#setting-api-key").focus();
    return;
  }
  if (apiKey && apiKey.length < 15) {
    statusEl.className = "settings-status err";
    statusEl.textContent = "API Key 太短，请检查是否完整粘贴";
    $("#setting-api-key").focus();
    return;
  }

  statusEl.textContent = "保存中…";
  try {
    const resp = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        api_key: $("#setting-api-key").value,
        base_url: $("#setting-base-url").value,
        model: $("#setting-model").value,
      }),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || resp.statusText);
    }
    const result = await resp.json();
    $("#model-badge").textContent = result.model || "";
    statusEl.className = "settings-status ok";
    statusEl.textContent = "设置已保存，模型已切换";
    setTimeout(() => {
      $("#settings-overlay").classList.remove("open");
      statusEl.textContent = "";
    }, 1200);
  } catch (e) {
    statusEl.className = "settings-status err";
    statusEl.textContent = "保存失败：" + e.message;
  }
};

// ===== 顶栏模型快速选择器 =====
(function () {
  const pill = document.getElementById("model-pill");
  const menu = document.getElementById("model-menu");
  const selector = document.getElementById("model-selector");
  if (!pill || !menu || !selector) return;

  const closeMenu = () => { menu.hidden = true; };

  // 根据当前模型名高亮对应预设
  function markActive() {
    const cur = (document.getElementById("model-badge").textContent || "").trim();
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
      const badge = document.getElementById("model-badge");
      const prev = badge.textContent;
      badge.textContent = p.model;
      // 同步设置弹窗里的输入，保持一致
      const bu = document.getElementById("setting-base-url");
      const md = document.getElementById("setting-model");
      if (bu) bu.value = p.base_url;
      if (md) md.value = p.model;
      if (typeof markPreset === "function") markPreset(btn.dataset.provider);
      try {
        const resp = await fetch("/api/settings", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
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

  const more = document.getElementById("model-menu-more");
  if (more) {
    more.addEventListener("click", () => {
      closeMenu();
      const ov = document.getElementById("settings-overlay");
      if (ov) ov.classList.add("open");
      if (typeof loadSettings === "function") loadSettings();
    });
  }

  document.addEventListener("click", (e) => { if (!selector.contains(e.target)) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeMenu(); });
})();
