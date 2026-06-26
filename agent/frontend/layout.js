/* 布局自定义（workspace 与 solve 两页共用）：预设布局 + 面板显隐，偏好存 localStorage。

   用法：
     MMLayout.init({
       containerSel: "#ws",          // 网格容器（应用 data-layout）
       gridSel: ".ws-grid",          // 真正的 grid 元素
       mountSel: ".ws-brand",        // 「布局」入口按钮挂载点（其后插入）
       storageKey: "ws-layout",
       gutterSel: ".ws-gutter",      // 拖拽手柄（仅 default 三栏全显时可用）
       regions: [
         { key: "left",  sel: ".ws-left", label: "文件树", fixed: "var(--col-left)" },
         { key: "mid",   sel: ".ws-mid",  label: "编辑器", flexible: true },
         { key: "right", sel: ".ws-right",label: "对话",   fixed: "var(--col-right)" },
       ],
       presets: [
         { key: "default",      label: "默认",     order: ["left","mid","right"], wide: "mid",  hidden: [] },
         { key: "chat-center",  label: "聊天居中", order: ["left","right","mid"], wide: "right",hidden: [] },
         { key: "focus-editor", label: "专注编辑", order: ["left","mid"],         wide: "mid",  hidden: ["right"] },
         { key: "focus-chat",   label: "聚焦对话", order: ["mid","right"],         wide: "right",hidden: ["left"] },
       ],
       onChange: () => {}  // 布局变化后回调（如 editor.refresh）
     });
*/
(function () {
  "use strict";

  function init(cfg) {
    const container = document.querySelector(cfg.containerSel);
    const grid = document.querySelector(cfg.gridSel);
    const mount = document.querySelector(cfg.mountSel);
    if (!container || !grid || !mount) return;

    const presets = cfg.presets;
    const regions = cfg.regions;
    const gutters = cfg.gutterSel ? Array.from(document.querySelectorAll(cfg.gutterSel)) : [];

    // 状态：当前预设 + 用户对各面板的显隐覆盖
    const saved = readState(cfg.storageKey);
    let presetKey = saved.preset || presets[0].key;
    let hiddenOverride = saved.hidden || {};   // { regionKey: true } 用户手动隐藏

    // ── 「布局」入口按钮 + 下拉 ──
    const wrap = document.createElement("div");
    wrap.className = "lp-wrap";
    const btn = document.createElement("button");
    btn.className = "lp-btn";
    btn.title = "调整布局";
    btn.innerHTML = "⊞ 布局";
    const menu = document.createElement("div");
    menu.className = "lp-menu";
    menu.hidden = true;
    wrap.append(btn, menu);
    mount.insertAdjacentElement("afterend", wrap);

    function buildMenu() {
      menu.innerHTML = "";
      const t1 = document.createElement("div");
      t1.className = "lp-menu-title";
      t1.textContent = "预设布局";
      menu.appendChild(t1);
      presets.forEach((p) => {
        const b = document.createElement("button");
        b.className = "lp-opt" + (p.key === presetKey ? " active" : "");
        b.textContent = p.label;
        b.addEventListener("click", () => {
          presetKey = p.key;
          hiddenOverride = {};   // 切预设时清掉手动显隐
          apply(); buildMenu();
        });
        menu.appendChild(b);
      });
      const t2 = document.createElement("div");
      t2.className = "lp-menu-title";
      t2.textContent = "显示面板";
      menu.appendChild(t2);
      const preset = presets.find((p) => p.key === presetKey) || presets[0];
      regions.forEach((r) => {
        const visible = isVisible(r.key, preset);
        const row = document.createElement("label");
        row.className = "lp-check";
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.checked = visible;
        cb.addEventListener("change", () => {
          // 记录用户覆盖（相对预设默认取反）
          const presetHidden = (preset.hidden || []).includes(r.key);
          if (cb.checked === !presetHidden) delete hiddenOverride[r.key];
          else hiddenOverride[r.key] = !cb.checked;
          apply();
        });
        row.append(cb, document.createTextNode(" " + r.label));
        menu.appendChild(row);
      });
    }

    function isVisible(key, preset) {
      if (key in hiddenOverride) return !hiddenOverride[key];
      return !(preset.hidden || []).includes(key);
    }

    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
    });
    document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) menu.hidden = true; });

    // 应用布局
    function apply() {
      const preset = presets.find((p) => p.key === presetKey) || presets[0];
      container.setAttribute("data-layout", presetKey);

      // 各面板显隐
      const visibleKeys = preset.order.filter((k) => isVisible(k, preset));
      regions.forEach((r) => {
        const el = document.querySelector(r.sel);
        if (!el) return;
        const vis = isVisible(r.key, preset);
        el.classList.toggle("lp-hidden", !vis);
        el.style.display = vis ? "" : "none";
      });

      // 是否处于「原生默认」：default 预设 + 三栏全显 → 保留 gutter 拖拽与 var 宽度，
      // 且不接管 order，一切回退到 DOM 原始顺序。
      const isNativeDefault =
        presetKey === presets[0].key &&
        regions.every((r) => isVisible(r.key, preset));

      if (isNativeDefault) {
        grid.style.gridTemplateColumns = "";   // 回退到 CSS 默认（含 6px gutter 轨道）
        gutters.forEach((g) => { g.style.display = ""; g.style.order = ""; });
        regions.forEach((r) => { const el = document.querySelector(r.sel); if (el) el.style.order = ""; });
      } else {
        gutters.forEach((g) => { g.style.display = "none"; });
        // 按可见顺序设 order，并拼列宽：宽栏 = 1fr，其余 = 各自固定宽
        regions.forEach((r) => {
          const el = document.querySelector(r.sel);
          if (!el) return;
          const pos = preset.order.indexOf(r.key);
          el.style.order = pos >= 0 ? String(pos) : "";
        });
        const cols = visibleKeys.map((k) => {
          const r = regions.find((x) => x.key === k);
          if (k === preset.wide || (r && r.flexible && !visibleKeys.includes(preset.wide))) {
            return "minmax(0, 1fr)";
          }
          if (r && r.fixed) return r.fixed;
          return "minmax(0, 1fr)";
        });
        grid.style.gridTemplateColumns = cols.join(" ");
      }

      writeState(cfg.storageKey, { preset: presetKey, hidden: hiddenOverride });
      if (typeof cfg.onChange === "function") setTimeout(cfg.onChange, 30);
    }

    buildMenu();
    apply();
    window.addEventListener("resize", () => { if (typeof cfg.onChange === "function") cfg.onChange(); });
  }

  function readState(key) {
    try { return JSON.parse(localStorage.getItem(key) || "{}") || {}; }
    catch { return {}; }
  }
  function writeState(key, val) {
    try { localStorage.setItem(key, JSON.stringify(val)); } catch { /* 忽略 */ }
  }

  window.MMLayout = { init };
})();
