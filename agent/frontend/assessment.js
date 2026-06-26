/* 入门测评页：开场（做测评 / 自选等级）→ 单选答题 → 定级 + 推荐学习路径。
   定级结果写入档案 assessment 字段；路径里程碑节点点击跳回 /learn 并打开对应知识点。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (window.MMRender ? MMRender.escapeHtml(s) : String(s || ""));

  // 模块字母 → 中文名（与知识库 taxonomy 对齐，仅用于薄弱点展示）
  const MODULE_NAME = {
    A: "论文写作", B: "新手入门", C: "常用建模方法", D: "模型求解算法",
    E: "数据预处理与统计", F: "竞赛策略", G: "结果可视化", H: "编程工具",
    I: "模型验证", J: "数学基础",
  };
  const DIFF_LABEL = { Beginner: "入门", Intermediate: "进阶", Advanced: "高阶" };

  let profile = null;
  let levels = [];        // [{level,name,tagline}]
  let quiz = null;        // {count, questions:[{id,options,perm,...}]}
  let answers = {};       // qid -> 展示顺序下选中的下标
  let idx = 0;            // 当前题号

  function show(view) {
    ["view-intro", "view-quiz", "view-result"].forEach((id) => { $(id).hidden = id !== view; });
    $("asm-loading").hidden = true;
  }

  function mdInto(host, text) {
    const div = document.createElement("div");
    MMRender.renderMarkdown(div, text || "");
    host.appendChild(div);
    return div;
  }

  // ── 开场视图：做测评 or 自选等级 ──────────────────────────────
  function renderIntro() {
    const host = $("view-intro");
    const hasQuiz = !!(quiz && quiz.count);
    host.innerHTML =
      `<div class="asm-hero">` +
      `<div class="asm-hero-badge">📋</div>` +
      `<h1 class="asm-hero-title">先给自己定个级</h1>` +
      `<p class="asm-hero-sub">知识点很多，不知道从哪学起？做个小测评，我们按你的水平推荐一条学习路径。也可以直接自己选。</p>` +
      `</div>` +
      `<div class="asm-choice">` +
      (hasQuiz
        ? `<button class="asm-choice-card press" id="go-quiz">` +
          `<div class="asm-choice-ico">✏️</div>` +
          `<h2>做测评定级</h2>` +
          `<p>${quiz.count} 道单选题，约 ${Math.max(5, Math.round(quiz.count / 3))} 分钟。按表现自动定级。</p>` +
          `<span class="asm-choice-cta">开始测评 →</span>` +
          `</button>`
        : `<div class="asm-choice-card asm-choice-card--off">` +
          `<div class="asm-choice-ico">✏️</div>` +
          `<h2>做测评定级</h2>` +
          `<p class="muted">测评题库尚未生成，先自己选一个等级吧。</p>` +
          `</div>`) +
      `<button class="asm-choice-card press" id="go-self">` +
      `<div class="asm-choice-ico">🎚️</div>` +
      `<h2>我自己选等级</h2>` +
      `<p>凭感觉选一个等级，直接拿到对应的学习路径，随时能重测。</p>` +
      `<span class="asm-choice-cta">选择等级 →</span>` +
      `</button>` +
      `</div>` +
      `<div class="asm-self" id="asm-self" hidden></div>`;

    if (hasQuiz) $("go-quiz").onclick = startQuiz;
    $("go-self").onclick = () => renderSelfSelect();
    show("view-intro");
    if (window.MMAnim) MMAnim.reveal(host, { observe: false });
  }

  function renderSelfSelect() {
    const box = $("asm-self");
    box.hidden = false;
    box.innerHTML =
      `<h3 class="asm-self-h">选择一个最贴近你的等级</h3>` +
      `<div class="asm-levels">` +
      levels.map((lv) =>
        `<button class="asm-level-card press" data-level="${esc(lv.level)}">` +
        `<span class="asm-level-code">${esc(lv.level)}</span>` +
        `<span class="asm-level-name">${esc(lv.name)}</span>` +
        `<span class="asm-level-tag">${esc(lv.tagline || "")}</span>` +
        `</button>`
      ).join("") +
      `</div>`;
    box.querySelectorAll(".asm-level-card").forEach((b) => {
      b.onclick = () => selfSelect(b.dataset.level);
    });
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function selfSelect(level) {
    try {
      const r = await fetch("/api/assessment/self-select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid: profile.id, level }),
      });
      if (!r.ok) throw new Error();
      const data = await r.json();
      renderResult({ level: data.level, level_name: data.level_name, source: "self", path: data.path });
    } catch {
      alert("保存失败，请重试。");
    }
  }

  // ── 答题视图 ─────────────────────────────────────────────────
  function startQuiz() {
    answers = {};
    idx = 0;
    renderQuestion();
    show("view-quiz");
  }

  function updateProgress() {
    if (!quiz) return;
    const answered = Object.keys(answers).length;
    $("asm-progress").innerHTML = `已答 <b>${answered}</b> / ${quiz.count}`;
  }

  function renderQuestion() {
    const host = $("view-quiz");
    const q = quiz.questions[idx];
    const chosen = answers[q.id];
    const pct = Math.round((idx / quiz.count) * 100);

    host.innerHTML =
      `<div class="asm-quiz-bar"><i style="width:${pct}%"></i></div>` +
      `<div class="asm-qmeta">` +
      `<span class="asm-qnum">第 ${idx + 1} / ${quiz.count} 题</span>` +
      `<span class="asm-qtags">` +
      `<span class="rtag rtag-cat">${esc(MODULE_NAME[q.module_id] || q.module_id)}</span>` +
      `<span class="rtag rtag-diff">${esc(DIFF_LABEL[q.difficulty] || q.difficulty)}</span>` +
      `</span></div>` +
      `<div class="asm-stem" id="asm-stem"></div>` +
      `<div class="asm-options" id="asm-options"></div>` +
      `<div class="asm-nav">` +
      `<button class="asm-btn-ghost" id="asm-prev"${idx === 0 ? " disabled" : ""}>← 上一题</button>` +
      (idx === quiz.count - 1
        ? `<button class="asm-btn-primary" id="asm-finish">交卷看结果</button>`
        : `<button class="asm-btn-primary" id="asm-next">下一题 →</button>`) +
      `</div>`;

    mdInto($("asm-stem"), q.stem);

    const optHost = $("asm-options");
    q.options.forEach((opt, i) => {
      const btn = document.createElement("button");
      btn.className = "asm-option press" + (chosen === i ? " chosen" : "");
      btn.innerHTML =
        `<span class="asm-option-key">${String.fromCharCode(65 + i)}</span>` +
        `<span class="asm-option-text"></span>`;
      MMRender.renderMarkdown(btn.querySelector(".asm-option-text"), opt);
      btn.onclick = () => {
        answers[q.id] = i;
        optHost.querySelectorAll(".asm-option").forEach((b) => b.classList.remove("chosen"));
        btn.classList.add("chosen");
        updateProgress();
      };
      optHost.appendChild(btn);
    });

    const prev = $("asm-prev");
    if (prev) prev.onclick = () => { if (idx > 0) { idx -= 1; renderQuestion(); } };
    const next = $("asm-next");
    if (next) next.onclick = () => { idx = Math.min(quiz.count - 1, idx + 1); renderQuestion(); };
    const fin = $("asm-finish");
    if (fin) fin.onclick = finishQuiz;

    updateProgress();
    host.scrollTop = 0;
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function finishQuiz() {
    const unanswered = quiz.count - Object.keys(answers).length;
    if (unanswered > 0) {
      if (!confirm(`还有 ${unanswered} 题没作答，未作答按答错计。确定交卷？`)) return;
    }
    const payload = quiz.questions.map((q) => ({
      id: q.id,
      choice: answers[q.id] != null ? answers[q.id] : -1,
      perm: q.perm,
    }));
    $("asm-progress").innerHTML = "判分中…";
    try {
      const r = await fetch("/api/assessment/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pid: profile.id, answers: payload }),
      });
      if (!r.ok) throw new Error();
      const result = await r.json();
      renderResult(result);
    } catch {
      alert("提交失败，请重试。");
      $("asm-progress").innerHTML = "";
    }
  }

  // ── 结果 + 推荐路径视图 ──────────────────────────────────────
  function renderResult(result) {
    $("asm-progress").innerHTML = "";
    const host = $("view-result");
    host.innerHTML = "";

    const isTest = result.source !== "self";
    const head = document.createElement("div");
    head.className = "asm-result-head";
    head.innerHTML =
      `<div class="asm-result-badge asm-lv-${esc(result.level)}">${esc(result.level)}</div>` +
      `<div class="asm-result-meta">` +
      `<div class="asm-result-eyebrow">${isTest ? "你的测评结果" : "你选择的等级"}</div>` +
      `<h1 class="asm-result-title">${esc(result.level)} · ${esc(result.level_name || "")}</h1>` +
      (isTest && result.total
        ? `<p class="asm-result-score">答对 ${result.correct} / ${result.total} · 得分率 ${Math.round((result.score || 0) * 100)}%</p>`
        : `<p class="asm-result-score">已为你准备好这个阶段的学习路径</p>`) +
      `</div>`;
    host.appendChild(head);

    // 薄弱模块（仅测评）
    if (isTest && result.per_module && Object.keys(result.per_module).length) {
      const weak = Object.entries(result.per_module)
        .filter(([, v]) => v < 0.6)
        .sort((a, b) => a[1] - b[1])
        .slice(0, 4);
      if (weak.length) {
        const cal = document.createElement("div");
        cal.className = "rcallout rcallout--warn asm-weak";
        cal.innerHTML =
          `<div class="rcallout-h">⚠️ 这几块还比较薄弱，路径里重点关注</div>` +
          `<div class="asm-weak-list">` +
          weak.map(([mid, v]) =>
            `<span class="asm-weak-chip">${esc(MODULE_NAME[mid] || mid)} <b>${Math.round(v * 100)}%</b></span>`
          ).join("") +
          `</div>`;
        host.appendChild(cal);
      }
    }

    // 推荐学习路径
    const path = result.path;
    if (path) {
      const sec = document.createElement("div");
      sec.className = "asm-path";
      let html = `<div class="asm-path-head"><h2>🗺️ 推荐学习路径：${esc(path.name)}</h2>`;
      if (path.tagline) html += `<p class="asm-path-tagline">${esc(path.tagline)}</p>`;
      html += `</div>`;
      sec.innerHTML = html;
      if (path.intro) mdInto(sec, path.intro).className = "asm-path-intro";

      const timeline = document.createElement("div");
      timeline.className = "asm-timeline";
      (path.milestones || []).forEach((m, i) => {
        const step = document.createElement("div");
        step.className = "asm-ms";
        const concepts = (m.concepts || []).map((c) =>
          `<button class="asm-ms-concept press" data-id="${esc(c.id)}" title="去学这个知识点">` +
          `<span class="asm-ms-concept-t">${esc(c.title)}</span>` +
          `<span class="asm-ms-concept-d">${esc(DIFF_LABEL[c.difficulty] || "")}</span>` +
          `</button>`
        ).join("");
        step.innerHTML =
          `<div class="asm-ms-dot">${i + 1}</div>` +
          `<div class="asm-ms-body">` +
          `<h3 class="asm-ms-title">${esc(m.title)}</h3>` +
          (m.desc ? `<p class="asm-ms-desc">${esc(m.desc)}</p>` : "") +
          (concepts ? `<div class="asm-ms-concepts">${concepts}</div>`
                    : `<p class="muted asm-ms-empty">这一步暂无具体知识点</p>`) +
          `</div>`;
        timeline.appendChild(step);
      });
      sec.appendChild(timeline);
      host.appendChild(sec);

      // 点知识点 → 跳学习模式并自动打开该知识点
      sec.querySelectorAll(".asm-ms-concept").forEach((b) => {
        b.onclick = () => { location.href = `/learn?open=${encodeURIComponent(b.dataset.id)}`; };
      });
    } else {
      const p = document.createElement("p");
      p.className = "muted";
      p.style.padding = "20px 0";
      p.textContent = "该等级的学习路径尚未生成。";
      host.appendChild(p);
    }

    // 答案解析（仅测评，有逐题明细时）
    if (isTest && Array.isArray(result.review) && result.review.length) {
      renderReview(host, result.review);
    }

    // 底部操作
    const footer = document.createElement("div");
    footer.className = "asm-result-foot";
    footer.innerHTML =
      `<a class="asm-btn-primary" href="/learn">进入学习模式 →</a>` +
      `<button class="asm-btn-ghost" id="asm-retake">重新测评 / 改等级</button>`;
    host.appendChild(footer);
    $("asm-retake").onclick = () => { renderIntro(); };

    show("view-result");
    window.scrollTo({ top: 0, behavior: "smooth" });
    if (window.MMAnim) MMAnim.reveal(host, { observe: false });
  }

  // 逐题答案解析：默认折叠，可一键展开；错题默认展开、标红
  function renderReview(host, review) {
    const wrongN = review.filter((r) => !r.correct).length;
    const wrap = document.createElement("section");
    wrap.className = "asm-review";
    wrap.innerHTML =
      `<div class="asm-review-head">` +
      `<h2>📝 答案解析</h2>` +
      `<div class="asm-review-tools">` +
      `<span class="asm-review-stat">错 <b>${wrongN}</b> / ${review.length}</span>` +
      `<label class="asm-review-only"><input type="checkbox" id="asm-only-wrong"${wrongN ? " checked" : ""}/> 只看错题</label>` +
      `</div></div>` +
      `<div class="asm-review-list" id="asm-review-list"></div>`;
    host.appendChild(wrap);

    const list = wrap.querySelector("#asm-review-list");

    function draw(onlyWrong) {
      list.innerHTML = "";
      review.forEach((r, qi) => {
        if (onlyWrong && r.correct) return;
        const item = document.createElement("div");
        item.className = "asm-rv-item" + (r.correct ? " ok" : " bad");

        const opts = r.options.map((opt, i) => {
          const isAns = i === r.answer;
          const isChosen = i === r.chosen;
          let cls = "asm-rv-opt";
          if (isAns) cls += " ans";
          if (isChosen && !r.correct) cls += " wrong-pick";
          const tag = isAns ? "✓ 正确" : (isChosen ? "你选的" : "");
          const optBox = document.createElement("div");
          optBox.className = cls;
          optBox.innerHTML = `<span class="asm-rv-key">${String.fromCharCode(65 + i)}</span>` +
            `<span class="asm-rv-otext"></span>` +
            (tag ? `<span class="asm-rv-tag">${tag}</span>` : "");
          MMRender.renderMarkdown(optBox.querySelector(".asm-rv-otext"), opt);
          return optBox.outerHTML;
        }).join("");

        item.innerHTML =
          `<div class="asm-rv-q">` +
          `<span class="asm-rv-badge">${r.correct ? "✓" : "✗"}</span>` +
          `<span class="asm-rv-num">第 ${qi + 1} 题</span>` +
          `<span class="asm-rv-stem-t"></span>` +
          `</div>` +
          `<div class="asm-rv-opts">${opts}</div>` +
          (r.explain ? `<div class="asm-rv-explain"><b>解析：</b>${esc(r.explain)}</div>` : "") +
          (r.concept_id
            ? `<button class="asm-rv-learn press" data-id="${esc(r.concept_id)}">📖 去学：${esc(r.concept_title || "相关知识点")}</button>`
            : "");
        MMRender.renderMarkdown(item.querySelector(".asm-rv-stem-t"), r.stem);
        list.appendChild(item);
      });
      if (!list.children.length) {
        list.innerHTML = `<p class="muted" style="padding:12px">全部答对，没有错题 🎉</p>`;
      }
      list.querySelectorAll(".asm-rv-learn").forEach((b) => {
        b.onclick = () => { location.href = `/learn?open=${encodeURIComponent(b.dataset.id)}`; };
      });
    }

    draw(!!wrongN);
    wrap.querySelector("#asm-only-wrong").onchange = (e) => draw(e.target.checked);
  }

  // ── 初始化 ───────────────────────────────────────────────────
  async function init() {
    profile = await Profile.require();
    if (!profile) return;

    try {
      const [statusR, quizR] = await Promise.all([
        fetch("/api/assessment/status"),
        fetch("/api/assessment/quiz"),
      ]);
      const status = await statusR.json();
      levels = status.levels || [];
      if (quizR.ok) quiz = await quizR.json();
    } catch {
      levels = [];
    }

    // 已定级的用户直接看结果；否则进开场
    const asm = profile.assessment || {};
    const params = new URLSearchParams(location.search);
    if (asm.level && params.get("retake") !== "1") {
      try {
        const r = await fetch(`/api/assessment/path/${asm.level}`);
        const path = r.ok ? await r.json() : null;
        renderResult({
          level: asm.level,
          level_name: (levels.find((l) => l.level === asm.level) || {}).name || "",
          source: asm.source || "self",
          score: asm.score,
          correct: null,
          total: null,
          per_module: asm.per_module,
          path,
        });
        return;
      } catch { /* 落到开场 */ }
    }
    renderIntro();
  }

  init();
})();
