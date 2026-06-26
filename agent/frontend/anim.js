/* ====================================================================
   anim.js —— 全站动效工具 window.MMAnim
   无依赖、尊重 prefers-reduced-motion。配合 anim.css 的 keyframes/工具 class。
   ==================================================================== */
(function () {
  "use strict";

  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  /* ---- 工具：卡片网格级联入场 ----
     给容器加 .stagger，子项按序号 --i 延迟淡入上移。可选 IntersectionObserver
     仅在进入视口时才触发（长列表用），默认直接播放（多数网格在首屏附近）。 */
  function reveal(target, opts) {
    opts = opts || {};
    const el = typeof target === "string" ? document.querySelector(target) : target;
    if (!el) return;
    const items = Array.from(el.children);
    if (reduced) { items.forEach((c) => { c.style.opacity = ""; c.style.transform = ""; }); return; }
    items.forEach((c, i) => c.style.setProperty("--i", String(i)));

    if ("IntersectionObserver" in window && opts.observe !== false) {
      // 先压住（不可见），进入视口再加 .stagger 触发级联
      items.forEach((c) => { c.style.opacity = "0"; });
      const io = new IntersectionObserver((ents) => {
        ents.forEach((e) => {
          if (e.isIntersecting) {
            e.target.style.opacity = "";
            e.target.classList.add("stagger-item");
            io.unobserve(e.target);
          }
        });
      }, { threshold: 0.06 });
      items.forEach((c) => io.observe(c));
    } else {
      el.classList.add("stagger");
    }
  }

  /* ---- 工具：视图切换（出场淡出 → 入场上移淡入），替换裸 hidden 切换 ---- */
  function transitionView(hideEl, showEl, done) {
    if (typeof hideEl === "string") hideEl = document.querySelector(hideEl);
    if (typeof showEl === "string") showEl = document.querySelector(showEl);
    const finish = () => {
      if (hideEl) hideEl.hidden = true;
      if (showEl) {
        showEl.hidden = false;
        if (!reduced) { showEl.classList.remove("anim-rise"); void showEl.offsetWidth; showEl.classList.add("anim-rise"); }
      }
      if (typeof done === "function") done();
    };
    if (reduced || !hideEl || hideEl.hidden) { finish(); return; }
    hideEl.style.transition = "opacity .16s var(--ease-out)";
    hideEl.style.opacity = "0";
    setTimeout(() => { hideEl.style.opacity = ""; hideEl.style.transition = ""; finish(); }, 150);
  }

  /* ---- 工具：给元素打入场动画（一次性） ---- */
  function play(el, cls) {
    if (!el) return;
    cls = cls || "anim-rise-sm";
    if (reduced) return;
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);
  }

  /* ---- 工具：AI 思考三点占位元素 ---- */
  function thinkingEl() {
    const d = document.createElement("div");
    d.className = "mm-thinking";
    d.innerHTML = "<i></i><i></i><i></i>";
    return d;
  }

  /* ---- 工具：复制成功 ✓ 短暂反馈 ---- */
  function flashCheck(btn, ms) {
    if (!btn) return;
    const old = btn.textContent;
    btn.textContent = "✓";
    btn.classList.add("mm-grow-check");
    setTimeout(() => { btn.textContent = old; btn.classList.remove("mm-grow-check"); }, ms || 1100);
  }

  /* ---- 母题：环境点阵连线网络（从 landing 抽出，可配置密度/透明度/鼠标交互） ----
     opts: { density, linkDist, mouseDist, inkAlpha, dotAlpha, interactive } */
  function ambientNetwork(canvas, opts) {
    if (typeof canvas === "string") canvas = document.querySelector(canvas);
    if (!canvas || reduced) return { stop() {} };
    const ctx = canvas.getContext("2d");
    opts = opts || {};
    const INK = "43, 45, 48", ROUGE = "192, 72, 81";
    const LINK_DIST = opts.linkDist || 130;
    const MOUSE_DIST = opts.mouseDist || 190;
    const INK_ALPHA = opts.inkAlpha != null ? opts.inkAlpha : 0.16;   // 连线最大透明度
    const DOT_ALPHA = opts.dotAlpha != null ? opts.dotAlpha : 0.5;    // 点透明度
    const DENSITY = opts.density || 15000;                            // 数值越大点越稀
    const interactive = opts.interactive !== false;

    let w = 0, h = 0, dpr = 1, points = [], raf = 0, alive = true;
    const mouse = { x: -9999, y: -9999, active: false };

    function size() {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      w = rect.width || window.innerWidth;
      h = rect.height || window.innerHeight;
      canvas.width = w * dpr; canvas.height = h * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
    }
    function seed() {
      const count = Math.round(Math.min(opts.maxPoints || 96, Math.max(opts.minPoints || 24, (w * h) / DENSITY)));
      points = [];
      for (let i = 0; i < count; i++) {
        points.push({
          x: Math.random() * w, y: Math.random() * h,
          vx: (Math.random() - 0.5) * 0.4, vy: (Math.random() - 0.5) * 0.4,
          r: Math.random() * 1.5 + 1.0,
        });
      }
    }
    function step() {
      if (!alive) return;
      ctx.clearRect(0, 0, w, h);
      for (const p of points) {
        p.x += p.vx; p.y += p.vy;
        if (p.x <= 0 || p.x >= w) p.vx *= -1;
        if (p.y <= 0 || p.y >= h) p.vy *= -1;
        p.x = Math.max(0, Math.min(w, p.x)); p.y = Math.max(0, Math.min(h, p.y));
      }
      for (let i = 0; i < points.length; i++) {
        for (let j = i + 1; j < points.length; j++) {
          const a = points[i], b = points[j];
          const dx = a.x - b.x, dy = a.y - b.y, d = Math.hypot(dx, dy);
          if (d < LINK_DIST) {
            ctx.strokeStyle = "rgba(" + INK + ", " + ((1 - d / LINK_DIST) * INK_ALPHA).toFixed(3) + ")";
            ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
          }
        }
      }
      if (interactive && mouse.active) {
        for (const p of points) {
          const dx = p.x - mouse.x, dy = p.y - mouse.y, d = Math.hypot(dx, dy);
          if (d < MOUSE_DIST) {
            ctx.strokeStyle = "rgba(" + ROUGE + ", " + ((1 - d / MOUSE_DIST) * 0.55).toFixed(3) + ")";
            ctx.lineWidth = 1.1;
            ctx.beginPath(); ctx.moveTo(p.x, p.y); ctx.lineTo(mouse.x, mouse.y); ctx.stroke();
            p.x -= dx * 0.004; p.y -= dy * 0.004;
          }
        }
        ctx.fillStyle = "rgba(" + ROUGE + ", 0.9)";
        ctx.beginPath(); ctx.arc(mouse.x, mouse.y, 3, 0, Math.PI * 2); ctx.fill();
      }
      ctx.fillStyle = "rgba(" + INK + ", " + DOT_ALPHA + ")";
      for (const p of points) { ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill(); }
      raf = requestAnimationFrame(step);
    }

    const onResize = () => size();
    const onMove = (e) => { mouse.x = e.clientX; mouse.y = e.clientY; mouse.active = true; };
    const onOut = () => { mouse.active = false; };
    const onTouch = (e) => { if (e.touches[0]) { mouse.x = e.touches[0].clientX; mouse.y = e.touches[0].clientY; mouse.active = true; } };
    window.addEventListener("resize", onResize);
    if (interactive) {
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseout", onOut);
      window.addEventListener("touchmove", onTouch, { passive: true });
      window.addEventListener("touchend", onOut);
    }
    size(); raf = requestAnimationFrame(step);

    return {
      stop() {
        alive = false; cancelAnimationFrame(raf);
        window.removeEventListener("resize", onResize);
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseout", onOut);
        window.removeEventListener("touchmove", onTouch);
        window.removeEventListener("touchend", onOut);
      },
    };
  }

  window.MMAnim = { reduced, reveal, transitionView, play, thinkingEl, flashCheck, ambientNetwork };
})();
