/* 分步做题引擎（共享）：按建模框架阶段，每一步用对话推进 + modality 受限输入
   （要点步只给对话框、公式步展开公式面板、求解步展开代码面板）+ 掌握度评估。

   真题练习（practice.js）与工作台「做题」模式（workspace.js）共用本模块。
   依赖：render.js（window.MMRender）。后端接口对静态题与动态题通用：
   /api/problems/{pid}/step-chat、/assess、/api/practice/run、/api/practice/upload。

   用法：StepFlow.renderSteps(containerEl, publicProblem, {
            profileId,                 // 用户档案 id（决定每步独立工作目录）
            stepState,                 // { [stepId]: {mastery, passed} } 已有进度（可空）
            onMastery(stepId, data),   // 某步评估完成回调（用于外部同步进度/总进度条）
         });
*/
(function () {
  const esc = (s) => MMRender.escapeHtml(s);

  const MODALITY_LABEL = {
    "key-points": "要点拆解", formula: "公式建模", code: "编程求解", prose: "分析论述",
  };
  const MODALITY_PLACEHOLDER = {
    "key-points": "分点说说你拆出的要点，我们一起看齐不齐…",
    formula: "写下你的符号定义与公式（用 $...$ 包裹 LaTeX），不确定就先说思路…",
    code: "说说你的求解思路或贴上关键代码，我们一起调…",
    prose: "说说你的分析和结论，哪怕不完整也行，我们一点点来…",
  };
  const MASTERY_CLASS = { "待加强": "m-low", "基本掌握": "m-mid", "很好": "m-high" };

  // run_id 规则需与后端 _practice_run_id 保持一致
  function practiceRunId(profileId, problemId, stepId) {
    const clean = (s) => (s || "").replace(/[^A-Za-z0-9_-]/g, "");
    return `practice_${clean(profileId).slice(0, 12)}_${clean(problemId)}_${clean(stepId)}`;
  }

  function renderSteps(container, problem, opts) {
    opts = opts || {};
    const stepState = opts.stepState || {};
    (problem.steps || []).forEach((step, idx) => {
      container.appendChild(buildStep(problem, step, idx, stepState[step.id], opts));
    });
  }

  // ---------- 单步：对话式 ----------
  function buildStep(problem, step, idx, prevState, opts) {
    const pid = problem.id;                  // 题目 id（静态或动态）
    const profileId = opts.profileId || "";
    const wrap = document.createElement("div");
    wrap.className = "pstep";
    wrap.dataset.stepId = step.id;

    const promptEl = document.createElement("div");
    promptEl.className = "pstep-prompt panel-md";
    MMRender.renderMarkdown(
      promptEl,
      `**${esc(step.title || "第 " + (idx + 1) + " 步")}**\n\n${step.prompt}`
    );
    const h = promptEl.querySelector("strong");
    if (h) {
      const mlabel = MODALITY_LABEL[step.modality];
      if (mlabel) h.insertAdjacentHTML("afterend", ` <span class="pstep-modality">${mlabel}</span>`);
      const mb = document.createElement("span");
      mb.className = "pstep-mastery";
      h.insertAdjacentElement("afterend", mb);
      renderMasteryBadge(mb, prevState && prevState.mastery);
    }
    wrap.appendChild(promptEl);

    if (step.hint) {
      const hint = document.createElement("details");
      hint.className = "pstep-hint";
      hint.innerHTML = `<summary>💡 提示</summary><div></div>`;
      MMRender.renderMarkdown(hint.querySelector("div"), step.hint);
      wrap.appendChild(hint);
    }

    // 对话区
    const messages = [];   // 本步师生对话，前端持有
    const chat = document.createElement("div");
    chat.className = "pstep-chat";
    const log = document.createElement("div");
    log.className = "chat-log";
    chat.appendChild(log);

    addBubble(log, "assistant",
      idx === 0
        ? "我们先一起把这道题理清楚——分点说说你读出的已知条件、要回答的子问题和关键约束吧，不确定也没关系，我们一点点拟定。"
        : "准备好了就说说你对这一步的想法吧～ 不确定也没关系，我们一点点来。");

    const runId = practiceRunId(profileId, pid, step.id);
    const uploadedFiles = [];

    const inputRow = document.createElement("div");
    inputRow.className = "chat-inputrow";
    const ta = document.createElement("textarea");
    ta.className = "chat-input";
    ta.rows = 2;
    ta.placeholder = MODALITY_PLACEHOLDER[step.modality] || "说说你的想法…";
    const send = document.createElement("button");
    send.className = "chat-send";
    send.textContent = "发送";
    inputRow.appendChild(ta);
    inputRow.appendChild(send);

    // 输入工具栏
    const tools = document.createElement("div");
    tools.className = "composer-tools";
    const btnUpload = mkToolBtn("📎 上传文件");
    const btnFormula = mkToolBtn("🧮 公式");
    const btnCode = mkToolBtn("▶ 代码");
    const chips = document.createElement("span");
    chips.className = "composer-chips";
    const fileInput = document.createElement("input");
    fileInput.type = "file";
    fileInput.hidden = true;
    tools.append(btnUpload, btnFormula, btnCode, chips, fileInput);
    chat.appendChild(tools);

    // 公式面板（LaTeX 实时预览）
    const fpanel = document.createElement("div");
    fpanel.className = "composer-formula";
    fpanel.hidden = true;
    const flatex = document.createElement("textarea");
    flatex.className = "composer-latex";
    flatex.rows = 2;
    flatex.placeholder = "输入 LaTeX，如 \\frac{dN}{dt}=rN(1-N/K)";
    const fprev = document.createElement("div");
    fprev.className = "composer-preview panel-md";
    const finsert = document.createElement("button");
    finsert.className = "composer-mini";
    finsert.textContent = "插入到输入框";
    fpanel.append(flatex, fprev, finsert);
    chat.appendChild(fpanel);
    let ftimer = null;
    flatex.addEventListener("input", () => {
      clearTimeout(ftimer);
      ftimer = setTimeout(() => {
        const v = flatex.value.trim();
        MMRender.renderMarkdown(fprev, v ? "预览：$" + v + "$" : "");
      }, 300);
    });
    finsert.onclick = () => {
      const v = flatex.value.trim();
      if (!v) return;
      ta.value = (ta.value ? ta.value + " " : "") + "$" + v + "$";
      flatex.value = ""; fprev.innerHTML = ""; ta.focus();
    };

    // 代码面板（内联编辑 + 运行）
    const cpanel = document.createElement("div");
    cpanel.className = "composer-code";
    cpanel.hidden = true;
    const ccode = document.createElement("textarea");
    ccode.className = "composer-codearea";
    ccode.rows = 8; ccode.spellcheck = false;
    ccode.placeholder = "在这里写 Python，点运行看结果（已预置 numpy/pandas/matplotlib）…";
    ccode.onkeydown = (e) => {
      if (e.key === "Tab") {
        e.preventDefault();
        const s = ccode.selectionStart, en = ccode.selectionEnd;
        ccode.value = ccode.value.slice(0, s) + "    " + ccode.value.slice(en);
        ccode.selectionStart = ccode.selectionEnd = s + 4;
      }
    };
    const crow = document.createElement("div");
    crow.className = "composer-coderow";
    const crun = document.createElement("button");
    crun.className = "composer-mini"; crun.textContent = "▶ 运行";
    const csend = document.createElement("button");
    csend.className = "composer-mini"; csend.textContent = "发给助教";
    const cstatus = document.createElement("span");
    cstatus.className = "pstep-status";
    crow.append(crun, csend, cstatus);
    const cout = document.createElement("div");
    cout.className = "composer-runout";
    cout.hidden = true;
    cpanel.append(ccode, crow, cout);
    chat.appendChild(cpanel);

    let lastRun = null;
    crun.onclick = async () => {
      const code = ccode.value;
      if (!code.trim()) { cstatus.textContent = "先写点代码"; return; }
      crun.disabled = true; cstatus.textContent = "运行中…";
      try {
        const r = await fetch("/api/practice/run", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_id: runId, code }),
        });
        const d = await r.json();
        lastRun = d;
        renderRunOutput(cout, d);
        cstatus.textContent = d.success ? "运行成功" : (d.timed_out ? "超时" : "有错误");
      } catch { cstatus.textContent = "运行失败"; }
      finally { crun.disabled = false; }
    };
    csend.onclick = () => {
      const code = ccode.value.trim();
      if (!code) { cstatus.textContent = "先写点代码"; return; }
      let msg = "我写了这一步的代码：\n\n```python\n" + code + "\n```";
      if (lastRun) {
        const out = ((lastRun.stdout || "") + (lastRun.stderr ? "\n" + lastRun.stderr : "")).trim();
        if (out) msg += "\n\n运行结果：\n```\n" + out.slice(0, 1500) + "\n```";
        if (lastRun.images && lastRun.images.length) msg += `\n（另生成了 ${lastRun.images.length} 张图）`;
      }
      submitChat(msg);
    };

    btnUpload.onclick = () => fileInput.click();
    fileInput.onchange = (e) => { const f = e.target.files[0]; if (f) uploadStepFile(f); e.target.value = ""; };
    btnFormula.onclick = () => { fpanel.hidden = !fpanel.hidden; if (!fpanel.hidden) flatex.focus(); };
    btnCode.onclick = () => { cpanel.hidden = !cpanel.hidden; if (!cpanel.hidden) ccode.focus(); };

    async function uploadStepFile(file) {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("pid", profileId);
      fd.append("problem_id", pid);
      fd.append("step_id", step.id);
      const chip = document.createElement("span");
      chip.className = "composer-chip";
      chip.textContent = "⏳ " + file.name;
      chips.appendChild(chip);
      try {
        const r = await fetch("/api/practice/upload", { method: "POST", body: fd });
        if (!r.ok) {
          const e = await r.json().catch(() => ({}));
          chip.textContent = "✗ " + (e.detail || file.name); chip.classList.add("err");
          return;
        }
        const d = await r.json();
        if (!uploadedFiles.includes(d.filename)) uploadedFiles.push(d.filename);
        chip.textContent = "📎 " + d.filename;
      } catch {
        chip.textContent = "✗ " + file.name; chip.classList.add("err");
      }
    }

    // 按阶段默认展开最相关的输入（要点/论述步只给对话框，不摆代码编辑器）
    if (step.modality === "formula") fpanel.hidden = false;
    else if (step.modality === "code") cpanel.hidden = false;

    chat.appendChild(inputRow);
    wrap.appendChild(chat);

    // 评估区
    const assessBar = document.createElement("div");
    assessBar.className = "pstep-assessbar";
    const assessBtn = document.createElement("button");
    assessBtn.className = "pstep-assess";
    assessBtn.textContent = "完成本步，让 AI 评估";
    const assessStatus = document.createElement("span");
    assessStatus.className = "pstep-status";
    assessBar.appendChild(assessBtn);
    assessBar.appendChild(assessStatus);
    wrap.appendChild(assessBar);

    const result = document.createElement("div");
    result.className = "pstep-result";
    result.hidden = true;
    wrap.appendChild(result);

    async function submitChat(forced) {
      const q = (forced !== undefined ? forced : ta.value).trim();
      if (!q) return;
      if (forced === undefined) ta.value = "";
      send.disabled = true;
      addBubble(log, "user", q);
      messages.push({ role: "user", content: q });
      let body = null;
      let acc = "";
      let thinking = addThinkingLine(log);
      const clearThinking = () => { if (thinking) { thinking.remove(); thinking = null; } };
      try {
        const resp = await fetch(`/api/problems/${encodeURIComponent(pid)}/step-chat`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            pid: profileId, step_id: step.id, messages, query: q,
            run_id: runId, workspace_files: uploadedFiles,
          }),
        });
        if (!resp.ok) {
          let detail = "请求失败";
          try { detail = (await resp.json()).detail || detail; } catch { /* 非 JSON */ }
          clearThinking();
          if (!body) body = addBubble(log, "assistant", "");
          MMRender.renderMarkdown(body, `_（${detail}）_`);
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const parts = buf.split("\n\n");
          buf = parts.pop();
          for (const part of parts) {
            const line = part.trim();
            if (!line.startsWith("data:")) continue;
            let ev;
            try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
            if (ev.type === "token") {
              clearThinking();
              if (!body) body = addBubble(log, "assistant", "");
              acc += ev.text;
              MMRender.renderMarkdown(body, acc);
              log.scrollTop = log.scrollHeight;
            } else if (ev.type === "tool_call") {
              clearThinking();
              addToolLine(log, "🔧 " + toolLabel(ev.name, ev.arguments));
              body = null; acc = "";
            } else if (ev.type === "tool_result") {
              if (ev.display && ev.display.images && ev.display.images.length) {
                addToolImages(log, ev.display.images);
              }
            } else if (ev.type === "done") {
              clearThinking();
              if (ev.content) { if (!body) body = addBubble(log, "assistant", ""); acc = ev.content; MMRender.renderMarkdown(body, acc); }
            } else if (ev.type === "error") {
              clearThinking();
              if (!body) body = addBubble(log, "assistant", "");
              acc += `\n\n_（出错：${ev.message}）_`;
              MMRender.renderMarkdown(body, acc);
            }
          }
        }
        messages.push({ role: "assistant", content: acc });
      } catch {
        clearThinking();
        if (!body) body = addBubble(log, "assistant", "");
        MMRender.renderMarkdown(body, acc + "\n\n_（请求失败，请重试）_");
      } finally {
        clearThinking();
        send.disabled = false;
      }
    }
    send.onclick = () => submitChat();
    ta.onkeydown = (e) => {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submitChat(); }
    };

    assessBtn.onclick = async () => {
      if (!messages.some((m) => m.role === "user")) {
        assessStatus.textContent = "先和 AI 聊聊你的思路再评估～";
        return;
      }
      assessBtn.disabled = true;
      assessStatus.textContent = "AI 评估中…";
      try {
        const r = await fetch(`/api/problems/${encodeURIComponent(pid)}/assess`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ pid: profileId, step_id: step.id, messages }),
        });
        if (!r.ok) throw new Error();
        const data = await r.json();
        renderAssess(result, data, step);
        assessStatus.textContent = data.error ? "评估失败" : `掌握度：${data.mastery}`;
        if (!data.error) {
          const mb = promptEl.querySelector(".pstep-mastery");
          if (mb) renderMasteryBadge(mb, data.mastery);
          if (typeof opts.onMastery === "function") opts.onMastery(step.id, data);
        }
      } catch {
        assessStatus.textContent = "评估请求失败，请重试";
      } finally {
        assessBtn.disabled = false;
      }
    };

    return wrap;
  }

  // ---------- 小工具 ----------
  function addBubble(log, role, text) {
    const m = document.createElement("div");
    m.className = "chat-msg chat-" + role + " anim-bubble";
    const body = document.createElement("div");
    body.className = "chat-body panel-md";
    MMRender.renderMarkdown(body, text);
    m.appendChild(body);
    log.appendChild(m);
    log.scrollTop = log.scrollHeight;
    return body;
  }

  function mkToolBtn(label) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "composer-tool";
    b.textContent = label;
    return b;
  }

  // AI 思考三点占位（首个 token / 工具事件到达前）
  function addThinkingLine(log) {
    const d = document.createElement("div");
    d.className = "chat-msg chat-assistant";
    if (window.MMAnim) d.appendChild(MMAnim.thinkingEl());
    else { const b = document.createElement("div"); b.className = "chat-body panel-md"; b.textContent = "…"; d.appendChild(b); }
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  function toolLabel(name, args) {
    args = args || {};
    if (name === "run_python") return "正在运行代码…";
    if (name === "read_document") return "正在读取文件 " + (args.filename || "");
    if (name === "read_file") return "正在读取 " + (args.path || "");
    if (name === "list_dir") return "正在查看目录…";
    if (name === "search_knowledge") return "正在检索知识库：" + (args.query || "");
    return "正在调用 " + name + "…";
  }

  function addToolLine(log, text) {
    const d = document.createElement("div");
    d.className = "chat-tool";
    d.textContent = text;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  function addToolImages(log, images) {
    const box = document.createElement("div");
    box.className = "chat-tool-imgs";
    for (const img of images) {
      const el = document.createElement("img");
      el.src = "data:image/png;base64," + img.data_base64;
      el.alt = img.name || "figure";
      box.appendChild(el);
    }
    log.appendChild(box);
    log.scrollTop = log.scrollHeight;
  }

  function renderRunOutput(el, d) {
    el.hidden = false;
    el.innerHTML = "";
    const out = ((d.stdout || "") + (d.stderr ? "\n" + d.stderr : "")).trim();
    if (out) {
      const pre = document.createElement("pre");
      pre.className = "runout-text" + (d.success ? "" : " err");
      pre.textContent = out;
      el.appendChild(pre);
    } else if (!d.images || !d.images.length) {
      const p = document.createElement("div");
      p.className = "muted";
      p.textContent = d.timed_out ? "运行超时。" : "（无文本输出）";
      el.appendChild(p);
    }
    for (const img of d.images || []) {
      const im = document.createElement("img");
      im.src = "data:image/png;base64," + img.data_base64;
      im.alt = img.name || "figure";
      im.className = "runout-img";
      el.appendChild(im);
    }
  }

  function renderMasteryBadge(el, mastery) {
    if (!mastery) { el.textContent = ""; el.className = "pstep-mastery"; return; }
    el.className = "pstep-mastery " + (MASTERY_CLASS[mastery] || "");
    el.textContent = (mastery === "待加强" ? "○ " : "✓ ") + mastery;
  }

  function renderAssess(el, data, step) {
    el.hidden = false;
    el.innerHTML = "";
    if (window.MMAnim && !MMAnim.reduced) { el.classList.remove("anim-rise"); void el.offsetWidth; el.classList.add("anim-rise"); }
    if (data.error) {
      el.innerHTML = `<div class="pstep-err">${esc(data.comment)}</div>`;
      return;
    }
    const head = document.createElement("div");
    head.className = "presult-head";
    head.innerHTML = `<span class="presult-mastery ${MASTERY_CLASS[data.mastery] || ""}">${esc(data.mastery)}</span>
      <span class="presult-pass">${data.passed ? '本步过关 <span class="mm-grow-check">✓</span>' : "再加把劲 ○"}</span>`;
    el.appendChild(head);

    if (data.comment) {
      const oc = document.createElement("div");
      oc.className = "presult-overall panel-md";
      MMRender.renderMarkdown(oc, "**点评**\n\n" + data.comment);
      el.appendChild(oc);
    }
    if (data.suggestions && data.suggestions.length) {
      const sg = document.createElement("div");
      sg.className = "presult-overall panel-md";
      MMRender.renderMarkdown(sg, "**下一步可以怎么提升**\n\n" + data.suggestions.map((s) => `- ${s}`).join("\n"));
      el.appendChild(sg);
    }
    if (data.paper_points && data.paper_points.length) {
      const pp = document.createElement("details");
      pp.className = "presult-papers";
      pp.innerHTML = `<summary>🏅 优秀论文这一步怎么做（${data.paper_points.length} 篇）</summary>`;
      for (const item of data.paper_points) {
        const pts = item.points || [];
        if (!pts.length) continue;
        const box = document.createElement("div");
        box.className = "ppt-paper panel-md";
        MMRender.renderMarkdown(box, `**论文 ${esc(item.paper_id || "")}**\n\n` + pts.map((x) => `- ${x}`).join("\n"));
        pp.appendChild(box);
      }
      el.appendChild(pp);
    }
    if (data.reference_outline) {
      const ref = document.createElement("details");
      ref.className = "presult-ref";
      ref.innerHTML = `<summary>查看参考标尺</summary><div class="panel-md"></div>`;
      MMRender.renderMarkdown(ref.querySelector("div"), data.reference_outline);
      el.appendChild(ref);
    }
  }

  window.StepFlow = { renderSteps, buildStep, practiceRunId, renderMasteryBadge };
})();
