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
    document.body.classList.remove('practice-active');
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
      list.innerHTML = '<div class="practice-empty"><p>题库还是空的。</p><p class="muted">可以先到学习页复习模型，稍后再来选择题目。</p></div>';
      return;
    }

    list.innerHTML = '<h1 class="practice-h1">真题练习</h1><p class="practice-sub">从一道真题开始，逐步推导，获得针对当前步骤的反馈。</p>';
    const grid = document.createElement("div");
    grid.className = "practice-grid";
    const filters=document.createElement('div');filters.className='ui-filters';filters.innerHTML='<input id="practice-search" type="search" placeholder="搜索题名" aria-label="搜索题名"><select id="practice-year" aria-label="年份"><option value="">全部年份</option>'+[...new Set(items.map(i=>i.year).filter(Boolean))].sort().reverse().map(y=>'<option>'+esc(String(y))+'</option>').join('')+'</select><select id="practice-difficulty" aria-label="难度"><option value="">全部难度</option>'+['Beginner','Intermediate','Advanced'].map(d=>'<option value="'+d+'">'+diffLabel(d)+'</option>').join('')+'</select>';list.append(filters);
    items.sort((a,b)=>Number(!!progressByProblem[b.id])-Number(!!progressByProblem[a.id]));
    for (const it of items) {
      const pr = progressByProblem[it.id];
      const passed = pr ? pr.passed_count : 0;
      const badge = passed
        ? `<span class="pcard-score">已过关 ${passed}/${it.step_count}</span>`
        : `<span class="pcard-score new">未开始</span>`;
      const card = document.createElement("button");
      card.className = "pcard press";card.dataset.title=it.title.toLowerCase();card.dataset.year=it.year||"";card.dataset.difficulty=it.difficulty||"";
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
    const empty=document.createElement('p');empty.className='ui-state';empty.hidden=true;empty.textContent='没有符合条件的题目，请调整筛选。';list.append(empty);
    const filter=()=>{let visible=0;grid.querySelectorAll('.pcard').forEach(c=>{c.hidden=!(c.dataset.title.includes($('practice-search').value.trim().toLowerCase())&&(!$('practice-year').value||c.dataset.year===$('practice-year').value)&&(!$('practice-difficulty').value||c.dataset.difficulty===$('practice-difficulty').value));if(!c.hidden)visible++;});empty.hidden=!!visible;};filters.querySelectorAll('input,select').forEach(n=>n.addEventListener('input',filter));
    if (window.MMAnim) MMAnim.reveal(grid, { observe: false });
  }

  // ---------- 单题做题 ----------
  async function openProblem(pid) {
    document.body.classList.add('practice-active');
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
    const statement=document.createElement('details');statement.className='practice-statement';statement.innerHTML='<summary>完整题面与材料</summary>';statement.append(bg);head.append(statement);

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

    const layout=document.createElement('div');layout.className='ui-practice-layout';const nav=document.createElement('nav');nav.className='ui-practice-steps';nav.setAttribute('aria-label','练习步骤');const main=document.createElement('div');layout.append(nav,main);view.append(layout);const panels=[...view.querySelectorAll('.pstep')];panels.forEach(n=>main.append(n));
    const select=idx=>{panels.forEach((p,i)=>p.hidden=i!==idx);nav.querySelectorAll('button').forEach((b,i)=>{b.setAttribute('aria-current',i===idx?'step':'false');});if(current.steps[idx]){const url=new URL(location.href);url.searchParams.set('step_id',current.steps[idx].id);history.replaceState(null,'',url);}};
    const linkedStep=new URLSearchParams(location.search).get('step_id');
    const linkedIndex=current.steps.findIndex(s=>s.id===linkedStep);
    current.steps.forEach((s,i)=>{const b=document.createElement('button');b.textContent=(i+1)+'. '+s.title;b.onclick=()=>select(i);nav.append(b);});select(linkedIndex>=0?linkedIndex:Math.max(0,current.steps.findIndex(s=>!stepState[s.id]?.passed)));
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
