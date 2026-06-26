/* 模式1 学习模式：课程地图（模块卡片 → 子类手风琴 → 概念）+ 全屏学习卡片 + 进度。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => (window.MMRender ? MMRender.escapeHtml(s) : String(s || ""));
  const CAT_COLORS = [
    "#C04851", "#D9B611", "#5b8c7b", "#4a6fa5", "#9a6fb0",
    "#c97b3f", "#7b9a3f", "#3f9a9a", "#a55b7b", "#6b6b8c",
  ];

  let profile = null;
  let mapData = null;          // /api/map 返回的嵌套结构
  let catColor = {};
  let completed = new Set();
  let currentId = null;        // 阅读视图当前打开的知识点
  let openSubs = new Set();    // 已展开的子类 id
  let totalConcepts = 0;

  function colorForCat(cat) {
    if (!(cat in catColor)) {
      catColor[cat] = CAT_COLORS[Object.keys(catColor).length % CAT_COLORS.length];
    }
    return catColor[cat];
  }

  function diffLabel(d) {
    return { Beginner: "入门", Intermediate: "进阶", Advanced: "高阶" }[d] || d || "";
  }
  const DIFF_DOT = { Beginner: "beg", Intermediate: "int", Advanced: "adv" };

  function updateProgress() {
    const done = completed.size;
    const pct = totalConcepts ? Math.round((done / totalConcepts) * 100) : 0;
    $("progress").innerHTML = `已掌握 <b>${done}</b> / ${totalConcepts} · ${pct}%`;
  }

  // ── 课程地图渲染 ─────────────────────────────────────────────
  function countDone(concepts) {
    let n = 0;
    for (const c of concepts) if (completed.has(c.id)) n += 1;
    return n;
  }

  function conceptVisible(c, diffFilter, onlyTodo) {
    if (diffFilter && c.difficulty !== diffFilter) return false;
    if (onlyTodo && completed.has(c.id)) return false;
    return true;
  }

  function renderMap(animate) {
    const catFilter = $("filter-cat").value;
    const diffFilter = $("filter-diff").value;
    const onlyTodo = $("show-ext").checked;
    const filterActive = !!(catFilter || diffFilter || onlyTodo);

    const host = $("map");
    host.innerHTML = "";

    let shownModules = 0;
    for (const m of mapData.modules) {
      if (catFilter && m.name !== catFilter) continue;

      // 该模块下、当前筛选条件下可见的子类
      const subBlocks = [];
      let visibleInModule = 0;
      for (const sc of m.subcats) {
        const vis = sc.concepts.filter((c) => conceptVisible(c, diffFilter, onlyTodo));
        visibleInModule += vis.length;
        if (filterActive && vis.length === 0) continue; // 筛选时隐藏空子类
        const open = openSubs.has(sc.subcat_id) || filterActive;
        const done = countDone(sc.concepts);
        const items = (open ? vis : []).map((c) => {
          const isDone = completed.has(c.id);
          const isRec = recommendedIds.has(c.id);
          const dot = DIFF_DOT[c.difficulty] || "int";
          return (
            `<button class="concept-item press${isDone ? " done" : ""}${isRec ? " recommended" : ""}" data-id="${esc(c.id)}">` +
            `<span class="cdot cdot--${dot}" title="${esc(diffLabel(c.difficulty))}"></span>` +
            `<span class="cname">${esc(c.title)}</span>` +
            (isRec ? `<span class="crec" title="推荐学习路径">★</span>` : "") +
            `<span class="cdiff">${esc(diffLabel(c.difficulty))}</span>` +
            `<span class="cdone">✓</span>` +
            `</button>`
          );
        }).join("");
        subBlocks.push(
          `<div class="subcat${open ? " open" : ""}">` +
          `<button class="subcat-row" data-sub="${esc(sc.subcat_id)}">` +
          `<span class="subcat-caret">${open ? "▾" : "▸"}</span>` +
          `<span class="subcat-name">${esc(sc.name)}</span>` +
          `<span class="subcat-count">${done}/${sc.concepts.length}</span>` +
          `</button>` +
          `<div class="concept-list"${open ? "" : " hidden"}>${items}</div>` +
          `</div>`
        );
      }

      if (filterActive && visibleInModule === 0) continue; // 整模块无匹配则隐藏

      const color = colorForCat(m.name);
      const mdone = countDone(m.subcats.flatMap((sc) => sc.concepts));
      const pct = m.count ? Math.round((mdone / m.count) * 100) : 0;
      const card = document.createElement("section");
      card.className = "mod-card";
      card.style.setProperty("--mod", color);
      card.innerHTML =
        `<div class="mod-head">` +
        `<div class="mod-name"><span class="mod-id">${esc(m.module_id)}</span>${esc(m.name)}</div>` +
        `<div class="mod-prog"><span class="mod-prog-num">${mdone}/${m.count}</span>` +
        `<div class="mod-bar"><i style="width:${pct}%"></i></div></div>` +
        `</div>` +
        `<div class="mod-subs">${subBlocks.join("")}</div>`;
      host.appendChild(card);
      shownModules += 1;
    }

    if (shownModules === 0) {
      host.innerHTML = `<p class="map-empty muted">没有符合筛选条件的知识点，试试放宽筛选。</p>`;
    } else if (animate && window.MMAnim) {
      MMAnim.reveal(host, { observe: false });
    }
  }

  function toggleSub(sid) {
    if (openSubs.has(sid)) openSubs.delete(sid);
    else openSubs.add(sid);
    renderMap();
  }

  function populateCatFilter() {
    const sel = $("filter-cat");
    for (const m of mapData.modules) {
      const o = document.createElement("option");
      o.value = m.name;
      o.textContent = `${m.module_id} · ${m.name} (${m.count})`;
      sel.appendChild(o);
    }
  }

  // ── 全屏学习卡片 ─────────────────────────────────────────────
  const ROLE_LABEL = { writer: "✍️ 论文写作", modeler: "📐 建模", coder: "💻 编程" };

  // 每段元数据：图标 / 标题 / 是否提供「就这段问助教」
  const SEC_META = {
    when_to_use:    { icon: "🎯", label: "什么时候用它", ask: false },
    definition:     { icon: "📖", label: "严格定义", ask: true },
    math_principle: { icon: "🧮", label: "背后的数学", ask: true },
    step_by_step:   { icon: "🪜", label: "上手步骤", ask: false },
    worked_example: { icon: "✏️", label: "跟着做一遍", ask: true },
    pitfalls:       { icon: "⚠️", label: "避开这些坑", ask: true },
    tools:          { icon: "💻", label: "工具与代码", ask: false },
  };

  // 按 roles 决定章节顺序 / 强调 / 折叠（侧重点适配）
  function sectionPlan(roles) {
    const r = (roles || []).map((x) => String(x).toLowerCase());
    const isWriter = r.includes("writer");
    const isCode = r.includes("coder") || r.includes("modeler");
    if (isWriter && !isCode) {
      // 论文写作类：文字优先，代码折叠，公式靠后且仅在有内容时显示
      return {
        order: ["when_to_use", "definition", "step_by_step", "worked_example", "pitfalls", "math_principle", "tools"],
        emphasize: new Set(),
        collapse: new Set(["tools"]),
      };
    }
    if (isCode) {
      // 建模 / 编程类：公式 + 可运行代码置顶突出
      return {
        order: ["when_to_use", "definition", "math_principle", "step_by_step", "tools", "worked_example", "pitfalls"],
        emphasize: new Set(["math_principle", "tools"]),
        collapse: new Set(),
      };
    }
    // 默认：完整学习阶梯
    return {
      order: ["when_to_use", "definition", "math_principle", "step_by_step", "worked_example", "pitfalls", "tools"],
      emphasize: new Set(),
      collapse: new Set(),
    };
  }

  function hasContent(key, d) {
    if (key === "math_principle") return !!(d.math_principle || (d.formulas && d.formulas.length));
    if (key === "step_by_step") return !!(d.step_by_step && d.step_by_step.length);
    if (key === "pitfalls") return !!(d.pitfalls && d.pitfalls.length);
    return !!d[key];
  }

  function gotoAsk(q) {
    location.href = `/build/free?ask=${encodeURIComponent(q)}`;
  }

  function askQuery(title, kind) {
    const t = `「${title}」`;
    switch (kind) {
      case "definition": return `${t}的定义我没太理解，能用通俗的话再解释一遍、并说说每个术语是什么意思吗？`;
      case "math_principle": return `能把${t}背后的数学原理和公式一步步推导讲清楚吗？每一步为什么这样做？`;
      case "worked_example": return `能再带我手把手做一遍${t}的例子吗？最好换一道数学建模的题目。`;
      case "pitfalls": return `用${t}时常见的坑有哪些？怎么提前避免？能各举个例子吗？`;
      default: return `请系统讲解数学建模知识点${t}，从直觉到原理一步步说，并举一个竞赛里的应用例子。`;
    }
  }

  // 把一段 Markdown（含 $公式$ / ```代码```）渲染进一个新容器
  function mdInto(host, text, cls) {
    const div = document.createElement("div");
    if (cls) div.className = cls;
    MMRender.renderMarkdown(div, text || "");
    host.appendChild(div);
    return div;
  }

  function stripLeadNum(s) {
    return String(s).replace(/^\s*\d+\s*[.、)）]\s*/, "");
  }

  // 章节正文渲染分发
  function renderSectionBody(key, body, d) {
    if (key === "math_principle") {
      let md = d.math_principle || "";
      if (d.formulas && d.formulas.length) {
        const fs = d.formulas.map((f) => {
          let s = `$$${f.latex_code}$$`;
          if (f.variables) {
            const vs = Object.entries(f.variables).map(([k, v]) => `- $${k}$：${v}`).join("\n");
            s += "\n" + vs;
          }
          return s;
        }).join("\n\n");
        md += (md ? "\n\n" : "") + fs;
      }
      mdInto(body, md, "rsec-md");
      return;
    }
    if (key === "step_by_step") {
      const ol = document.createElement("ol");
      ol.className = "rsteps";
      d.step_by_step.forEach((s) => {
        const li = document.createElement("li");
        mdInto(li, stripLeadNum(s));
        ol.appendChild(li);
      });
      body.appendChild(ol);
      return;
    }
    if (key === "pitfalls") {
      const ul = document.createElement("ul");
      ul.className = "rpit rcallout rcallout--warn";
      d.pitfalls.forEach((s) => {
        const li = document.createElement("li");
        mdInto(li, stripLeadNum(s));
        ul.appendChild(li);
      });
      body.appendChild(ul);
      return;
    }
    // when_to_use / definition / tools 都是富文本
    mdInto(body, d[key], "rsec-md");
  }

  function addSection(host, key, d, plan, num) {
    const meta = SEC_META[key];
    const accent = plan.emphasize.has(key);
    const collapsed = plan.collapse.has(key);

    const wrap = document.createElement(collapsed ? "details" : "section");
    wrap.className = "rsec" + (accent ? " rsec--accent" : "") + (collapsed ? " rsec--fold" : "");

    const head = document.createElement(collapsed ? "summary" : "div");
    head.className = "rsec-head";
    head.innerHTML =
      `<span class="rsec-num">${num}</span>` +
      `<span class="rsec-ico">${meta.icon}</span>` +
      `<h2 class="rsec-label">${esc(meta.label)}</h2>` +
      (collapsed ? `<span class="rsec-fold-hint">展开 ▾</span>` : "");

    if (meta.ask) {
      const ab = document.createElement("button");
      ab.className = "rsec-ask";
      ab.type = "button";
      ab.textContent = "🛠 就这段问助教";
      ab.onclick = (e) => { e.preventDefault(); e.stopPropagation(); gotoAsk(askQuery(d.title, key)); };
      head.appendChild(ab);
    }

    const body = document.createElement("div");
    body.className = "rsec-body";
    renderSectionBody(key, body, d);

    wrap.appendChild(head);
    wrap.appendChild(body);
    host.appendChild(wrap);
  }

  // 同类知识点 / 真题 等导航块
  function navLinks(arr, emptyTxt) {
    if (!arr || !arr.length) return `<span class="muted">${emptyTxt}</span>`;
    return arr.map((x) =>
      `<button class="panel-link" data-id="${esc(x.id)}">${esc(x.title)}</button>`
    ).join("");
  }

  function renderReader(d) {
    currentId = d.chunk_id;
    const plan = sectionPlan(d.roles);

    // 顶栏标签 + 标题
    const tags = [];
    if (d.category) tags.push(`<span class="rtag rtag-cat">${esc(d.category)}</span>`);
    if (d.difficulty) tags.push(`<span class="rtag rtag-diff">${esc(diffLabel(d.difficulty))}</span>`);
    (d.roles || []).forEach((role) => {
      const m = ROLE_LABEL[String(role).toLowerCase()];
      if (m) tags.push(`<span class="rtag rtag-role">${m}</span>`);
    });
    tags.push(`<span class="rtag rtag-id">${esc(d.chunk_id)}</span>`);
    $("reader-tags").innerHTML = tags.join("");
    $("reader-title").textContent = d.title;

    const doneBtn = $("reader-done");
    doneBtn.style.display = "";
    syncDoneBtn(d.chunk_id);

    const body = $("reader-body");
    body.innerHTML = "";

    // 0. 一句话看懂（hero）
    if (d.one_liner) {
      const hero = document.createElement("div");
      hero.className = "rhero";
      hero.innerHTML = `<span class="rhero-tag">一句话看懂</span>`;
      mdInto(hero, d.one_liner, "rhero-text");
      body.appendChild(hero);
    }

    // 0.5 打个比方（直觉类比）
    if (d.intuition) {
      const cal = document.createElement("div");
      cal.className = "rcallout rcallout--idea";
      cal.innerHTML = `<div class="rcallout-h">💡 打个比方</div>`;
      mdInto(cal, d.intuition, "rcallout-body");
      body.appendChild(cal);
    }

    // 按 roles 顺序渲染各学习阶梯段（仅渲染有内容的）
    let num = 0;
    for (const key of plan.order) {
      if (!hasContent(key, d)) continue;
      num += 1;
      addSection(body, key, d, plan, num);
    }

    // 相关真题
    if (d.cases && d.cases.length) {
      const sec = document.createElement("section");
      sec.className = "rsec";
      sec.innerHTML = `<div class="rsec-head"><span class="rsec-ico">📂</span><h2 class="rsec-label">用到它的真题</h2></div>`;
      const chips = document.createElement("div");
      chips.className = "rcases";
      d.cases.slice(0, 30).forEach((c) => {
        const sp = document.createElement("span");
        sp.className = "rcase";
        sp.textContent = c;
        chips.appendChild(sp);
      });
      sec.appendChild(chips);
      body.appendChild(sec);
    }

    // 学习路径：所属分类面包屑 + 同类知识点（由易到难）
    const path = document.createElement("div");
    path.className = "rpath";
    const crumb = (d.taxonomy_path || []).slice(0, 2).map(esc).join(" <span class='rcrumb-sep'>›</span> ");
    path.innerHTML =
      `<div class="rpath-block"><div class="rpath-h">📍 所属分类</div><div class="rcrumb">${crumb || "—"}</div></div>` +
      `<div class="rpath-block"><div class="rpath-h">🧩 同类知识点（由易到难）</div><div class="rpath-list">${navLinks(d.siblings, "这一类暂时只有它")}</div></div>`;
    body.appendChild(path);
    path.querySelectorAll(".panel-link").forEach((b) => {
      b.onclick = () => openNode(b.dataset.id);
    });

    // 底部主行动：带这个知识点去问助教（核心功能）
    const footer = document.createElement("div");
    footer.className = "rfooter";
    const ask = document.createElement("button");
    ask.className = "rask-cta";
    ask.type = "button";
    ask.innerHTML = "🛠 带这个知识点去问助教";
    ask.onclick = () => gotoAsk(askQuery(d.title, "whole"));
    const hint = document.createElement("p");
    hint.className = "rfooter-hint muted";
    hint.textContent = "助教会结合这个知识点，一步步带你把它真正学会。";
    footer.appendChild(ask);
    footer.appendChild(hint);
    body.appendChild(footer);

    body.scrollTop = 0;
    updateRail();
  }

  function renderReaderUnavailable(node) {
    currentId = (node && node.id) || null;
    $("reader-tags").innerHTML = "";
    $("reader-title").textContent = (node && node.title) || "暂无内容";
    $("reader-done").style.display = "none";
    const body = $("reader-body");
    body.innerHTML =
      `<div class="rcallout rcallout--signal" style="margin:24px 0">` +
      `<div class="rcallout-h">📭 这个知识点暂时还没有内容</div>` +
      `<div class="rcallout-body">可以先学习同类的其它知识点，或回到地图换一个看看。</div>` +
      `</div>`;
    body.scrollTop = 0;
    updateRail();
  }

  function openReaderShell() {
    $("reader").hidden = false;
    document.body.classList.add("reader-open");
  }
  function closeReader() {
    const reader = $("reader");
    const finish = () => {
      reader.hidden = true;
      reader.classList.remove("reader-closing");
      document.body.classList.remove("reader-open");
      currentId = null;
    };
    if (window.MMAnim && !MMAnim.reduced && !reader.hidden) {
      reader.classList.add("reader-closing");
      setTimeout(finish, 200);
    } else {
      finish();
    }
  }
  function updateRail() {
    const b = $("reader-body");
    const max = b.scrollHeight - b.clientHeight;
    const pct = max > 0 ? (b.scrollTop / max) * 100 : 0;
    $("reader-rail").style.width = pct + "%";
  }

  async function openNode(id) {
    openReaderShell();
    currentId = id;
    $("reader-tags").innerHTML = "";
    $("reader-title").textContent = "加载中…";
    $("reader-done").style.display = "none";
    $("reader-body").innerHTML = '<p class="muted" style="padding:28px">加载中…</p>';

    let d;
    try {
      const r = await fetch(`/api/graph/node/${encodeURIComponent(id)}`);
      if (!r.ok) { renderReaderUnavailable({ id, title: id }); return; }
      d = await r.json();
    } catch {
      $("reader-body").innerHTML = '<p class="muted" style="padding:28px">加载失败。</p>';
      return;
    }
    if (currentId !== id) return; // 期间又点了别的知识点
    renderReader(d);
  }

  function syncDoneBtn(chunkId) {
    const btn = $("reader-done");
    if (!btn || currentId !== chunkId) return;
    const done = completed.has(chunkId);
    btn.classList.toggle("done", done);
    if (done) { btn.classList.remove("anim-pop"); void btn.offsetWidth; btn.classList.add("anim-pop"); }
    btn.textContent = done ? "✓ 已掌握（点击取消）" : "标记为已掌握";
  }

  async function toggleDone(chunkId) {
    const learned = !completed.has(chunkId);
    try {
      const r = await fetch(`/api/profiles/${profile.id}/learn`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chunk_id: chunkId, learned }),
      });
      if (!r.ok) throw new Error();
      const learn = await r.json();
      completed = new Set(learn.completed);
    } catch {
      alert("保存进度失败。");
      return;
    }
    syncDoneBtn(chunkId);
    updateProgress();
    renderMap(); // 刷新地图上的进度与勾选（reader 仍覆盖在上层）
    if (myLevel) renderPathBanner(); // 同步推荐路径进度
  }

  // ── 入门测评 / 学习路径接入 ─────────────────────────────────
  const LEVEL_NAME = { L1: "萌新", L2: "入门", L3: "进阶", L4: "熟练", L5: "高手" };
  const DIFF_LABEL = { Beginner: "入门", Intermediate: "进阶", Advanced: "高阶" };
  let myLevel = "";
  let myPath = null;                 // 当前等级的推荐路径
  let recommendedIds = new Set();    // 路径涉及的知识点 id（用于地图高亮）

  async function initAssessment() {
    const asm = (profile && profile.assessment) || {};
    myLevel = asm.level || "";

    // 顶部等级入口按钮：已定级显示等级，未定级显示「测评定级」
    const entry = $("asm-entry");
    if (entry) {
      entry.hidden = false;
      if (myLevel) {
        entry.innerHTML = `<span class="lv-badge lv-${myLevel}">${myLevel}</span>` +
          `<span class="lv-text">${LEVEL_NAME[myLevel] || ""} · 我的路径</span>`;
        entry.onclick = openPathPanel;
      } else {
        entry.innerHTML = `<span class="lv-text">📋 测评定级</span>`;
        entry.onclick = () => { location.href = "/assessment"; };
      }
    }

    // 已定级：拉取推荐路径，置顶展示 + 地图高亮
    if (myLevel) {
      try {
        const r = await fetch(`/api/assessment/path/${myLevel}`);
        if (r.ok) {
          myPath = await r.json();
          recommendedIds = new Set(
            (myPath.milestones || []).flatMap((m) => (m.concepts || []).map((c) => c.id))
          );
          renderPathBanner();
          renderMap();   // 重画以高亮推荐知识点
        }
      } catch { /* ignore */ }
    }

    // 深链：从测评结果页点知识点跳来，自动打开该知识点
    const params = new URLSearchParams(location.search);
    const open = params.get("open");
    if (open) {
      const url = new URL(location.href);
      url.searchParams.delete("open");
      history.replaceState(null, "", url.toString());
      openNode(open);
    }

    // 首次进入引导：未定级、未跳过、且没有深链时弹出
    if (!myLevel && !asm.skipped && !open) {
      const gate = $("asm-gate");
      if (gate) {
        gate.hidden = false;
        const skip = () => {
          gate.hidden = true;
          fetch("/api/assessment/skip", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ pid: profile.id }),
          }).catch(() => {});
        };
        $("asm-gate-skip").onclick = skip;
        $("asm-gate-scrim").onclick = skip;
      }
    }
  }

  // 知识地图顶部置顶「我的推荐路径」卡片
  function renderPathBanner() {
    if (!myPath) return;
    const host = $("asm-path-banner");
    if (!host) return;
    const total = recommendedIds.size;
    const doneN = [...recommendedIds].filter((id) => completed.has(id)).length;
    const pct = total ? Math.round((doneN / total) * 100) : 0;

    // 取前几个还没掌握的推荐知识点作为「接下来学」快捷入口
    const next = [];
    for (const m of (myPath.milestones || [])) {
      for (const c of (m.concepts || [])) {
        if (!completed.has(c.id)) next.push(c);
        if (next.length >= 6) break;
      }
      if (next.length >= 6) break;
    }
    const chips = next.map((c) =>
      `<button class="pb-chip press" data-id="${esc(c.id)}">` +
      `<span class="pb-chip-t">${esc(c.title)}</span>` +
      `<span class="pb-chip-d">${esc(DIFF_LABEL[c.difficulty] || "")}</span>` +
      `</button>`
    ).join("");

    host.hidden = false;
    host.innerHTML =
      `<div class="pb-head">` +
      `<div class="pb-title"><span class="lv-badge lv-${myLevel}">${myLevel}</span>` +
      `<span class="pb-title-t">为你推荐的学习路径 · ${esc(myPath.name || "")}</span></div>` +
      `<div class="pb-prog"><span class="pb-prog-num">${doneN}/${total}</span>` +
      `<div class="pb-bar"><i style="width:${pct}%"></i></div></div>` +
      `<button class="pb-more" id="pb-more">查看完整路径 →</button>` +
      `</div>` +
      (myPath.tagline ? `<p class="pb-tagline">${esc(myPath.tagline)}</p>` : "") +
      (chips
        ? `<div class="pb-next"><span class="pb-next-h">接下来学：</span><div class="pb-chips">${chips}</div></div>`
        : `<p class="pb-done">🎉 推荐路径上的知识点都掌握了，去地图里挑战更多吧。</p>`);

    host.querySelector("#pb-more").onclick = openPathPanel;
    host.querySelectorAll(".pb-chip").forEach((b) => {
      b.onclick = () => openNode(b.dataset.id);
    });
  }

  async function openPathPanel() {
    const panel = $("asm-pathpanel");
    const body = $("asm-pathpanel-body");
    panel.hidden = false;
    document.body.classList.add("reader-open");
    body.innerHTML = '<p class="muted" style="padding:24px">加载中…</p>';
    $("asm-pathpanel-close").onclick = closePathPanel;
    $("asm-pathpanel-scrim").onclick = closePathPanel;

    let path = null;
    try {
      const r = await fetch(`/api/assessment/path/${myLevel}`);
      if (r.ok) path = await r.json();
    } catch { /* ignore */ }

    if (!path) {
      body.innerHTML = '<p class="muted" style="padding:24px">这个等级的学习路径暂时还没有生成。</p>';
      return;
    }
    $("asm-pathpanel-title").innerHTML =
      `<span class="lv-badge lv-${myLevel}">${myLevel}</span> ${esc(path.name)} · 学习路径`;

    let html = "";
    if (path.tagline) html += `<p class="asm-pp-tagline">${esc(path.tagline)}</p>`;
    html += `<div class="asm-pp-timeline">`;
    (path.milestones || []).forEach((m, i) => {
      const concepts = (m.concepts || []).map((c) => {
        const done = completed.has(c.id);
        return `<button class="asm-pp-concept press${done ? " done" : ""}" data-id="${esc(c.id)}">` +
          `<span class="asm-pp-check">${done ? "✓" : ""}</span>` +
          `<span class="asm-pp-ctext">${esc(c.title)}</span>` +
          `<span class="asm-pp-cdiff">${esc(DIFF_LABEL[c.difficulty] || "")}</span>` +
          `</button>`;
      }).join("");
      const total = (m.concepts || []).length;
      const doneN = (m.concepts || []).filter((c) => completed.has(c.id)).length;
      html +=
        `<div class="asm-pp-ms">` +
        `<div class="asm-pp-dot">${i + 1}</div>` +
        `<div class="asm-pp-msbody">` +
        `<h3 class="asm-pp-mstitle">${esc(m.title)}` +
        (total ? `<span class="asm-pp-msprog">${doneN}/${total}</span>` : "") +
        `</h3>` +
        (m.desc ? `<p class="asm-pp-msdesc">${esc(m.desc)}</p>` : "") +
        (concepts ? `<div class="asm-pp-concepts">${concepts}</div>` : "") +
        `</div></div>`;
    });
    html += `</div>`;
    html += `<div class="asm-pp-foot"><a class="asm-btn-ghost" href="/assessment?retake=1">重新测评 / 改等级</a></div>`;
    body.innerHTML = html;

    body.querySelectorAll(".asm-pp-concept").forEach((b) => {
      b.onclick = () => { closePathPanel(); openNode(b.dataset.id); };
    });
  }

  function closePathPanel() {
    $("asm-pathpanel").hidden = true;
    if ($("reader").hidden) document.body.classList.remove("reader-open");
  }

  async function init() {
    profile = await Profile.require();
    if (!profile) return;
    completed = new Set((profile.learn && profile.learn.completed) || []);

    try {
      const r = await fetch("/api/map");
      mapData = await r.json();
    } catch {
      $("map").innerHTML = '<p class="map-empty muted">课程地图加载失败。</p>';
      return;
    }

    totalConcepts = mapData.modules.reduce((s, m) => s + m.count, 0);
    populateCatFilter();
    updateProgress();
    renderMap(true);

    ["filter-cat", "filter-diff", "show-ext"].forEach((id) => {
      $(id).addEventListener("change", () => renderMap(true));
    });

    // 地图点击委托：点子类标题展开/收起，点概念条目打开学习卡片
    $("map").addEventListener("click", (e) => {
      const sub = e.target.closest(".subcat-row");
      if (sub) { toggleSub(sub.dataset.sub); return; }
      const item = e.target.closest(".concept-item");
      if (item) { openNode(item.dataset.id); return; }
    });

    // 阅读视图交互
    $("reader-close").onclick = closeReader;
    $("reader-scrim").onclick = closeReader;
    $("reader-done").onclick = () => { if (currentId) toggleDone(currentId); };
    $("reader-body").addEventListener("scroll", updateRail);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && !$("reader").hidden) closeReader();
    });

    initAssessment();
  }

  init();
})();
