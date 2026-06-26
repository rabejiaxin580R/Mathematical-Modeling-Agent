/* 共享渲染工具：Markdown + 代码高亮 + KaTeX 公式 + 复制按钮。
   依赖全局 marked / hljs / renderMathInElement（由页面 CDN 引入）。
   供 learn.js / practice.js 使用；app.js 自带同名实现，互不影响。 */
(function () {
  if (window.marked && marked.setOptions) {
    marked.setOptions({
      breaks: true,
      highlight(code, lang) {
        try {
          if (window.hljs) {
            if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
            return hljs.highlightAuto(code).value;
          }
        } catch {}
        return code;
      },
    });
  }

  function escapeHtml(s) {
    return (s || "").replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

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

  // 先把代码块与公式从 Markdown 中保护出来：marked 会吞掉 \(…\) \[…\] 的反斜杠，
  // 导致 KaTeX 找不到定界符；公式里的 | 也会破坏表格。占位符用私有区字符包裹，绝不与正文冲突。
  function preprocessMath(src) {
    const codes = [];
    const maths = [];
    let s = src || "";
    s = s.replace(/```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`/g, (m) => {
      codes.push(m);
      return "c" + (codes.length - 1) + "";
    });
    const stash = (tex, display) => {
      maths.push({ tex, display });
      return "m" + (maths.length - 1) + "";
    };
    s = s
      .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
      .replace(/\\\[([\s\S]+?)\\\]/g, (_, t) => stash(t, true))
      .replace(/\\\(([\s\S]+?)\\\)/g, (_, t) => stash(t, false))
      .replace(/\$(?!\s)([^\n$]+?)(?<!\s)\$/g, (_, t) => stash(t, false));
    s = s.replace(/c(\d+)/g, (_, i) => codes[+i]);
    return { text: s, maths };
  }

  function restoreMath(html, maths) {
    return html.replace(/m(\d+)/g, (_, i) => {
      const mm = maths[+i];
      if (!mm) return "";
      const safe = escapeHtml(mm.tex);
      return mm.display ? "$$" + safe + "$$" : "$" + safe + "$";
    });
  }

  function renderMarkdown(el, text) {
    if (!window.marked) { el.innerHTML = escapeHtml(text || ""); return; }
    const pre = preprocessMath(text);
    el.innerHTML = restoreMath(marked.parse(pre.text), pre.maths);
    el.querySelectorAll("pre code").forEach((b) => {
      if (window.hljs && !b.classList.contains("hljs")) hljs.highlightElement(b);
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

  window.MMRender = { renderMarkdown, escapeHtml, addCopyButtons };
})();
