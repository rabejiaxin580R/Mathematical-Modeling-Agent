// ===== 用户引导流程 Onboarding Tour =====
// 可作为独立模块被任意页面引用，以浮层形式展示引导步骤

const OnboardingTour = (() => {
  // ---------- 步骤数据 ----------
  const STEPS = [
    {
      icon: "👋",
      title: "欢迎使用数学建模助教",
      desc: "基于 40 小时课程知识库的 AI 助教，帮你学建模、做题目、写代码。花 1 分钟快速了解核心功能。",
      accent: "#C04851"
    },
    {
      icon: "📚",
      title: "学习模式",
      desc: "顺着知识图谱，从基础概念一步步点亮。每个知识点都有详细讲解、类比说明和关联题目，适合系统性地掌握建模方法。",
      accent: "#5b8c7b"
    },
    {
      icon: "🛠️",
      title: "做建模",
      desc: "两种方式：\n• 自由练习工作台 — 集成 IDE、终端和 AI 对话，自己折腾\n• 做题 — 带一道题让助教拆成步骤，陪你一步步做",
      accent: "#D9B611"
    },
    {
      icon: "🎯",
      title: "真题练习",
      desc: "历年 HiMCM 真题分步练习。写完每一步提交，AI 会按评分要点给你打分和反馈，帮你看到差距在哪里、怎么提高。",
      accent: "#C04851"
    },
    {
      icon: "💬",
      title: "AI 对话助手",
      desc: "随时向助教提问！支持 Markdown 公式渲染、Python 代码编写与运行、文件上传（PDF/Word/CSV 等）和文档读取。",
      accent: "#3a6ea5"
    },
    {
      icon: "✨",
      title: "准备就绪",
      desc: "你已经了解了所有核心功能。点击下方入口直接开始，或进入主页选择你想走的路。",
      accent: "#C04851",
      isComplete: true
    }
  ];

  const TOTAL = STEPS.length;

  // ---------- 状态 ----------
  let currentStep = 0;         // 0-based
  let overlayEl = null;
  let cardEl = null;
  let dotsEl = null;
  let onFinishCallback = null;  // 引导完成后的回调
  let transitioning = false;

  // ---------- DOM 构建 ----------
  function createOverlay() {
    // 遮罩层
    overlayEl = document.createElement("div");
    overlayEl.className = "ob-overlay";
    overlayEl.innerHTML = `
      <div class="ob-backdrop"></div>
      <div class="ob-card" id="ob-card">
        <div class="ob-card-inner">
          <div class="ob-icon-wrap">
            <span class="ob-icon" id="ob-icon"></span>
          </div>
          <h2 class="ob-title" id="ob-title"></h2>
          <p class="ob-desc" id="ob-desc"></p>
          <div class="ob-dots" id="ob-dots"></div>
          <div class="ob-actions">
            <button class="ob-skip" id="ob-skip">跳过引导</button>
            <button class="ob-prev" id="ob-prev">← 上一步</button>
            <button class="ob-next" id="ob-next">下一步 →</button>
          </div>
        </div>
      </div>
    `;
    document.body.appendChild(overlayEl);

    // 首次展示即标记已触发，防止页面切换后重复弹出
    localStorage.setItem("ob_completed", "1");

    cardEl = document.getElementById("ob-card");
    dotsEl = document.getElementById("ob-dots");

    // 绑定事件
    document.getElementById("ob-skip").addEventListener("click", skipTour);
    document.getElementById("ob-prev").addEventListener("click", prevStep);
    document.getElementById("ob-next").addEventListener("click", nextStep);

    // 键盘导航
    const keyHandler = (e) => {
      if (!overlayEl || overlayEl.style.display === "none") return;
      if (e.key === "ArrowRight" || e.key === "Enter") nextStep();
      else if (e.key === "ArrowLeft") prevStep();
      else if (e.key === "Escape") skipTour();
    };
    document.addEventListener("keydown", keyHandler);
    overlayEl._keyHandler = keyHandler;

    // 点击遮罩不关闭（防止误触）
    overlayEl.querySelector(".ob-backdrop").addEventListener("click", (e) => e.stopPropagation());
  }

  function renderDots() {
    if (!dotsEl) return;
    dotsEl.innerHTML = "";
    for (let i = 0; i < TOTAL; i++) {
      const dot = document.createElement("span");
      dot.className = "ob-dot";
      if (i < currentStep) dot.classList.add("done");
      if (i === currentStep) dot.classList.add("cur");
      // 完成页的特殊圆点
      if (STEPS[currentStep].isComplete && i === currentStep) dot.classList.add("complete");
      dotsEl.appendChild(dot);
    }
  }

  function renderStep(stepIdx, direction) {
    if (transitioning) return;
    transitioning = true;

    const step = STEPS[stepIdx];
    const isComplete = !!step.isComplete;

    // 更新图标、标题、描述
    document.getElementById("ob-icon").textContent = step.icon;
    document.getElementById("ob-title").textContent = step.title;
    document.getElementById("ob-desc").innerHTML = step.desc.replace(/\n/g, "<br>");

    // 更新进度圆点
    renderDots();

    // 更新按钮状态
    const prevBtn = document.getElementById("ob-prev");
    const nextBtn = document.getElementById("ob-next");
    const skipBtn = document.getElementById("ob-skip");

    prevBtn.style.visibility = stepIdx === 0 ? "hidden" : "visible";

    if (isComplete) {
      nextBtn.textContent = "开始使用 →";
      nextBtn.classList.add("ob-next-finish");
      skipBtn.style.display = "none";
      prevBtn.style.display = "none";  // 完成页隐藏上一步
      cardEl.classList.add("ob-card-complete");

      // 添加快捷入口
      addQuickLinks();
    } else {
      nextBtn.textContent = "下一步 →";
      nextBtn.classList.remove("ob-next-finish");
      skipBtn.style.display = "";
      prevBtn.style.display = "";
      cardEl.classList.remove("ob-card-complete");
      removeQuickLinks();
    }

    // CSS 动画：方向性切换
    cardEl.style.transition = "none";
    cardEl.style.transform = direction === "next"
      ? "translateX(40px)"
      : direction === "prev"
        ? "translateX(-40px)"
        : "translateY(20px)";
    cardEl.style.opacity = "0";

    requestAnimationFrame(() => {
      cardEl.style.transition = "transform 0.28s cubic-bezier(0.22, 0.61, 0.36, 1), opacity 0.22s ease";
      cardEl.style.transform = "translateX(0) translateY(0)";
      cardEl.style.opacity = "1";
    });

    setTimeout(() => { transitioning = false; }, 300);
  }

  function addQuickLinks() {
    // 避免重复添加
    if (document.getElementById("ob-quick-links")) return;
    const inner = cardEl.querySelector(".ob-card-inner");
    const links = document.createElement("div");
    links.className = "ob-quick-links";
    links.id = "ob-quick-links";
    links.innerHTML = `
      <a class="ob-link ob-link-learn" href="/learn">📚 开始学习</a>
      <a class="ob-link ob-link-build" href="/build">🛠️ 做建模</a>
      <a class="ob-link ob-link-practice" href="/practice">🎯 练真题</a>
    `;
    // 插入到操作按钮区之前
    const actions = inner.querySelector(".ob-actions");
    inner.insertBefore(links, actions);
  }

  function removeQuickLinks() {
    const el = document.getElementById("ob-quick-links");
    if (el) el.remove();
  }

  // ---------- 导航 ----------
  function nextStep() {
    if (transitioning) return;
    if (currentStep < TOTAL - 1) {
      currentStep++;
      renderStep(currentStep, "next");
    } else {
      finishTour();
    }
  }

  function prevStep() {
    if (transitioning || currentStep === 0) return;
    currentStep--;
    renderStep(currentStep, "prev");
  }

  function skipTour() {
    if (transitioning) return;
    destroyOverlay();
    if (onFinishCallback) onFinishCallback();
  }

  function finishTour() {
    if (transitioning) return;
    destroyOverlay();
    if (onFinishCallback) onFinishCallback();
  }

  function destroyOverlay() {
    if (!overlayEl) return;
    if (overlayEl._keyHandler) {
      document.removeEventListener("keydown", overlayEl._keyHandler);
    }
    overlayEl.classList.add("ob-overlay-exit");
    setTimeout(() => {
      if (overlayEl && overlayEl.parentNode) {
        overlayEl.parentNode.removeChild(overlayEl);
      }
      overlayEl = null;
      cardEl = null;
      dotsEl = null;
    }, 350);
  }

  // ---------- 公开 API ----------
  function start(callback) {
    // 如果已完成过引导，直接回调
    if (localStorage.getItem("ob_completed") === "1") {
      if (callback) callback();
      return;
    }

    onFinishCallback = callback || null;
    currentStep = 0;

    // 如果已存在 overlay，先清理
    if (overlayEl && overlayEl.parentNode) {
      destroyOverlay();
      // 等待销毁动画完成后重新创建
      setTimeout(() => {
        createOverlay();
        renderStep(0, "init");
      }, 380);
      return;
    }

    createOverlay();
    renderStep(0, "init");
  }

  function reset() {
    localStorage.removeItem("ob_completed");
  }

  function isCompleted() {
    return localStorage.getItem("ob_completed") === "1";
  }

  return { start, reset, isCompleted };
})();

// 显式挂到 window，确保跨脚本可访问
window.OnboardingTour = OnboardingTour;
