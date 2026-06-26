/* 中栏「代码 / 预览」标签 + 导出 Word（workspace 与 solve 两页共用）。
   依赖：window.IDE.editor（CodeMirror 实例）、MMRender（render.js）、#filename（当前文件名）。
   - 预览：.md 用 MMRender 完整渲染；.tex 尽力渲染（KaTeX 处理公式片段）；其余提示不支持。
   - 导出 Word：把编辑器内容 + 扩展名 POST /api/export/docx，下载 .docx。
   - solve 页没有文件树（workspace.js 不在），这里自带 ai-file-write 监听把 AI 写出的文件载入编辑器。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const cm = () => (window.IDE && window.IDE.editor) || null;

  // 中栏编辑器容器：workspace 与 solve 都用 .ws-editor 包 <textarea id="editor">
  const editorWrap = document.querySelector(".ws-editor");
  if (!editorWrap) return;

  const isWorkspacePage = !!$("explorer-tree"); // 有文件树 = 独立练习工作台

  function curName() {
    const fn = $("filename");
    return (fn && fn.value.trim()) || "main.py";
  }
  function extOf(name) { return (name.split(".").pop() || "").toLowerCase(); }
  function fmtForExt(ext) {
    if (ext === "md" || ext === "markdown") return "markdown";
    if (ext === "tex" || ext === "latex") return "latex";
    return null; // 不支持预览/导出
  }

  // ── 注入标签条 + 预览容器 ──
  const bar = document.createElement("div");
  bar.className = "ep-bar";
  bar.innerHTML =
    `<button class="ep-tab active" data-tab="code">代码</button>` +
    `<button class="ep-tab" data-tab="preview">预览</button>` +
    `<span class="ep-spacer"></span>` +
    `<button class="ep-export" id="ep-export" title="把当前 .md/.tex 文档导出为 Word">⬇ 导出 Word</button>`;
  const preview = document.createElement("div");
  preview.className = "ep-preview panel-md";
  preview.hidden = true;
  editorWrap.insertBefore(bar, editorWrap.firstChild);
  editorWrap.appendChild(preview);

  let tab = "code";
  function setTab(next) {
    tab = next;
    bar.querySelectorAll(".ep-tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === next));
    const cmEl = editorWrap.querySelector(".CodeMirror");
    if (next === "preview") {
      renderPreview();
      preview.hidden = false;
      if (cmEl) cmEl.style.display = "none";
    } else {
      preview.hidden = true;
      if (cmEl) cmEl.style.display = "";
      const ed = cm(); if (ed) setTimeout(() => ed.refresh(), 30);
    }
  }

  function renderPreview() {
    const ed = cm();
    const content = ed ? ed.getValue() : "";
    const fmt = fmtForExt(extOf(curName()));
    if (fmt === "markdown") {
      MMRender.renderMarkdown(preview, content);
    } else if (fmt === "latex") {
      // 尽力预览：保留正文，交给 KaTeX auto-render 处理 $...$ / $$...$$
      preview.innerHTML =
        `<div class="ep-note">LaTeX 预览为尽力渲染（公式可显示，整体排版以编译为准）。</div>` +
        `<pre class="ep-tex"></pre>`;
      preview.querySelector(".ep-tex").textContent = content;
      if (window.renderMathInElement) {
        try {
          window.renderMathInElement(preview, {
            delimiters: [
              { left: "$$", right: "$$", display: true },
              { left: "$", right: "$", display: false },
              { left: "\\(", right: "\\)", display: false },
              { left: "\\[", right: "\\]", display: true },
            ],
            throwOnError: false,
          });
        } catch { /* 忽略渲染错误 */ }
      }
    } else {
      preview.innerHTML = `<div class="ep-note">仅 .md / .tex 文档支持预览。当前文件按代码编辑。</div>`;
    }
  }

  bar.querySelectorAll(".ep-tab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  // 在预览态下编辑器内容变化（如 AI 写入）时刷新预览
  (function watchEditor() {
    const ed = cm();
    if (ed && ed.on) ed.on("change", () => { if (tab === "preview") renderPreview(); });
  })();

  // ── 导出 Word ──
  $("ep-export").addEventListener("click", async () => {
    const ed = cm();
    if (!ed) return;
    const content = ed.getValue();
    const name = curName();
    const fmt = fmtForExt(extOf(name));
    if (!fmt) { alert("仅支持把 .md 或 .tex 文档导出为 Word。"); return; }
    if (!content.trim()) { alert("内容为空。"); return; }
    const btn = $("ep-export");
    const prev = btn.textContent;
    btn.disabled = true; btn.textContent = "导出中…";
    try {
      const r = await fetch("/api/export/docx", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, filename: name, format: fmt }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || r.status);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = name.replace(/\.[^.]+$/, "") + ".docx";
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      alert("导出失败：" + err.message);
    } finally {
      btn.disabled = false; btn.textContent = prev;
    }
  });

  // ── solve 页：把 AI write_file 写出的文件载入编辑器并按扩展名设语言 ──
  if (!isWorkspacePage) {
    window.addEventListener("ai-file-write", async (e) => {
      const path = e && e.detail && e.detail.path;
      if (!path) return;
      const name = path.replace(/[\\/]+$/, "").split(/[\\/]/).pop() || "file";
      try {
        const r = await (await fetch(`/api/ide/read?path=${encodeURIComponent(path)}`)).json();
        const ed = cm();
        if (!r.ok || !ed) return;
        ed.setValue(r.content || "");
        ed.setOption("mode", modeForExtLocal(name));
        const fn = $("filename"); if (fn) fn.value = name;
        if (tab === "preview") renderPreview();
      } catch { /* 忽略 */ }
    });
  }

  function modeForExtLocal(name) {
    const ext = extOf(name);
    const map = {
      py: "python", md: "markdown", markdown: "markdown",
      tex: "stex", latex: "stex", js: "javascript",
      json: { name: "javascript", json: true },
      html: "htmlmixed", htm: "htmlmixed", xml: "xml", css: "css",
    };
    return map[ext] || null;
  }

  window.EditorPreview = { setTab, renderPreview };
})();
