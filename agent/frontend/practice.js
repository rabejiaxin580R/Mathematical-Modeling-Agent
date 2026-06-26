/* 模式3 真题练习（对话式）：题库列表 + 单题做题。分步引擎在 stepflow.js（与工作台做题模式共用）。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => MMRender.escapeHtml(s);

  let profile = null;
  let progressByProblem = {};   // problem_id -> {steps:{step_id:{mastery,passed}}, passed_count}
  let current = null;           // 当前题 public 数据

  function diffLabel(d) {
    return { Beginner: "入门", Intermediate: "进阶", Advanced: "高阶" }[d] || d || "";
  }

  // ---------- 题库列表 ----------
  async function showList() {
    if (window.MMAnim) MMAnim.transitionView($("view-problem"), $("view-list"));
    else { $("view-problem").hidden = true; $("view-list").hidden = false; }
    $("btn-list").hidden = true;
    history.replaceState(null, "", "/practice");

    const list = $("view-list");
    list.innerHTML = '<p class="muted" style="padding:20px">加载题库中…</p>';
    let items;
    try {
      items = await (await fetch("/api/problems")).json();
    } catch {
      list.innerHTML = '<p class="muted" style="padding:20px">题库加载失败。</p>';
      return;
    }
    if (!items.length) {
      list.innerHTML = '<div class="practice-empty"><p>题库还是空的。</p><p class="muted">把真题 JSON 放进 <code>data/problems/</code> 即可出现在这里。</p></div>';
      return;
    }

    list.innerHTML = '<h1 class="practice-h1">真题练习</h1><p class="practice-sub">按建模框架一步步走，每一步和 AI 助教对话把它想透。助教会引导你、对照优秀论文，最后评估你这一步的掌握度。</p>';
    const grid = document.createElement("div");
    grid.className = "practice-grid";
    for (const it of items) {
      const pr = progressByProblem[it.id];
      const passed = pr ? pr.passed_count : 0;
      const badge = passed
        ? `<span class="pcard-score">已过关 ${passed}/${it.step_count}</span>`
        : `<span class="pcard-score new">未开始</span>`;
      const card = document.createElement("button");
      card.className = "pcard press";
      card.innerHTML = `
        <div class="pcard-top">
          ${it.year ? `<span class="pcard-year">${it.year}</span>` : ""}
          ${it.difficulty ? `<span class="pcard-diff">${diffLabel(it.difficulty)}</span>` : ""}
        </div>
        <h2 class="pcard-title">${esc(it.title)}</h2>
        <div class="pcard-tags">${(it.tags || []).map((t) => `<span>${esc(t)}</span>`).join("")}</div>
        <div class="pcard-foot">
          <span class="muted">${it.step_count} 步</span>
          ${badge}
        </div>`;
      card.onclick = () => openProblem(it.id);
      grid.appendChild(card);
    }
    list.appendChild(grid);
    if (window.MMAnim) MMAnim.reveal(grid, { observe: false });
  }

  // ---------- 单题做题 ----------
  async function openProblem(pid) {
    if (window.MMAnim) MMAnim.transitionView($("view-list"), $("view-problem"));
    else { $("view-list").hidden = true; $("view-problem").hidden = false; }
    $("btn-list").hidden = false;
    const view = $("view-problem");
    view.innerHTML = '<p class="muted" style="padding:20px">加载题目中…</p>';

    try {
      current = await (await fetch(`/api/problems/${encodeURIComponent(pid)}`)).json();
    } catch {
      view.innerHTML = '<p class="muted" style="padding:20px">题目加载失败。</p>';
      return;
    }
    history.replaceState(null, "", `/practice?p=${encodeURIComponent(pid)}`);

    const pr = progressByProblem[pid];
    const stepState = (pr && pr.steps) || {};

    view.innerHTML = "";
    // 题面
    const head = document.createElement("div");
    head.className = "problem-head";
    head.innerHTML = `<h1 class="problem-title">${esc(current.title)}</h1>
      <div class="problem-meta">
        ${current.year ? `<span>${current.year}</span>` : ""}
        ${current.difficulty ? `<span>${diffLabel(current.difficulty)}</span>` : ""}
        <span>共 ${current.steps.length} 步</span>
      </div>`;
    const bg = document.createElement("div");
    bg.className = "problem-bg panel-md";
    MMRender.renderMarkdown(bg, current.background);
    head.appendChild(bg);

    if (current.data_files && current.data_files.length) {
      const data = document.createElement("div");
      data.className = "problem-data";
      const label = document.createElement("span");
      label.className = "problem-data-label";
      label.textContent = "📎 题目数据文件";
      data.appendChild(label);
      for (const f of current.data_files) {
        const a = document.createElement("a");
        a.href = `/api/problems/${encodeURIComponent(pid)}/data/${encodeURIComponent(f.filename)}`;
        a.setAttribute("download", "");
        a.textContent = f.name || f.filename;
        data.appendChild(a);
      }
      head.appendChild(data);
    }
    view.appendChild(head);

    // 步骤（共享分步引擎）
    StepFlow.renderSteps(view, current, {
      profileId: profile.id,
      stepState,
      onMastery: onStepMastery,
    });

    // 进度条
    const totalBar = document.createElement("div");
    totalBar.className = "problem-total";
    totalBar.id = "problem-total";
    view.appendChild(totalBar);
    updateTotalBar();
  }

  // 某步评估完成 → 同步本地进度与总进度条
  function onStepMastery(stepId, data) {
    if (!current) return;
    const pr = progressByProblem[current.id] || { steps: {}, passed_count: 0 };
    pr.steps[stepId] = { mastery: data.mastery, passed: data.passed };
    pr.passed_count = Object.values(pr.steps).filter((s) => s.passed).length;
    progressByProblem[current.id] = pr;
    updateTotalBar();
  }

  function updateTotalBar() {
    const bar = $("problem-total");
    if (!bar || !current) return;
    const pr = progressByProblem[current.id];
    const passed = pr ? pr.passed_count : 0;
    const n = current.steps.length;
    const pct = n ? Math.round((passed / n) * 100) : 0;
    bar.innerHTML = `已过关 <b>${passed}</b> / ${n} 步（${pct}%）`;
  }

  async function init() {
    profile = await Profile.require();
    if (!profile) return;
    for (const a of (profile.practice && profile.practice.attempts) || []) {
      const steps = {};
      for (const s of a.steps || []) steps[s.step_id] = { mastery: s.mastery, passed: s.passed };
      progressByProblem[a.problem_id] = {
        steps,
        passed_count: a.passed_count || Object.values(steps).filter((s) => s.passed).length,
      };
    }
    $("btn-list").onclick = showList;

    const pid = new URLSearchParams(location.search).get("p");
    if (pid) openProblem(pid);
    else showList();
  }

  init();
})();
