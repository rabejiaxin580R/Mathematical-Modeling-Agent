/* 做题驾驶舱：上传/粘贴题面 → AI 拆步 → 点开每一步，和「建模共创伙伴」一起做。
   独立于自由练习：自己的页面、布局、后端人格（/api/solve/*）。
   复用：MMRender（render.js）渲染 Markdown/公式；Profile（profile.js）做门禁。 */
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const MOD = { "key-points": "要点拆解", formula: "公式建模", code: "编程求解", prose: "分析论述" };

  // ── 状态 ──
  let runId = null;          // 上传题目文件所在的工作目录（= 一个会话 id）
  const files = [];          // 已上传的题目文件名
  let problem = null;        // 拆步后的题目对象
  let problemConvId = null;  // 这道题的做题会话（一题一块上下文）
  let curStepIdx = -1;
  const seen = new Set();
  let streaming = false;
  let abort = null;
  let retryMessage = null;
  let activeChatRequest = null;
  let chatViewVersion = 0;
  let layoutController, saveQueue = Promise.resolve(), switching = false, restoring = false;
  const inputDrafts = {}, chatScroll = {};
  const attachmentStates = new Map();
  const attachmentKey = () => JSON.stringify([solveSessionId, problem?.steps[curStepIdx]?.id]);
  function attachmentState() {
    const key=attachmentKey();
    if(!attachmentStates.has(key))attachmentStates.set(key,{items:[],pending:0,error:'',retry:null,revision:0});
    return attachmentStates.get(key);
  }
  function attachmentBase(session,step) {
    return '/api/solve/sessions/'+encodeURIComponent(session)+'/steps/'+encodeURIComponent(step)+'/attachments';
  }
  function paintAttachments() {
    const box=$('sv-discussion-attachments'),state=attachmentState();
    box.replaceChildren();
    $('sv-discussion-attach').disabled=!solveSessionId || curStepIdx<0 || switching || streaming || state.pending>0;
    for(const item of state.items){
      const chip=document.createElement('span');chip.className='sv-attachment';
      const label=document.createElement('span');label.textContent=item.filename;label.title=item.filename;
      const remove=document.createElement('button');remove.type='button';remove.textContent='×';
      remove.title='移除 '+item.filename;remove.setAttribute('aria-label',remove.title);remove.disabled=streaming || state.pending>0;
      const session=solveSessionId,step=problem.steps[curStepIdx].id;
      remove.onclick=async()=>{
        state.revision++;
        state.pending++;paintAttachments();
        try{const res=await fetch(attachmentBase(session,step)+'/'+encodeURIComponent(item.id),{method:'DELETE'});
          if(!res.ok)throw new Error((await res.json()).detail || '附件暂时无法移除');
          state.items=state.items.filter(x=>x.id!==item.id);state.error='';
        }catch(e){state.error=e.message;}finally{state.pending--;if(session===solveSessionId && step===problem?.steps[curStepIdx]?.id)paintAttachments();}
      };
      chip.append(label,remove);box.appendChild(chip);
    }
    if(state.pending || state.error){
      const status=document.createElement('span');status.className='sv-attachment-status';status.setAttribute('role','status');
      status.textContent=state.pending ? '附件处理中…' : state.error;box.appendChild(status);
      if(state.retry && !state.pending){const retry=document.createElement('button');retry.type='button';retry.textContent='重试上传';retry.onclick=()=>uploadDiscussionFile(state.retry);box.appendChild(retry);}
    }
  }
  async function loadAttachments() {
    const session=solveSessionId,step=problem?.steps[curStepIdx]?.id,state=attachmentState();
    const revision=++state.revision;
    paintAttachments();
    if(!session || !step)return;
    try{const response=await fetch(attachmentBase(session,step));if(!response.ok)throw new Error('附件列表暂未载入，可切回本步重试');
      const items=await response.json();
      if(revision===state.revision){state.items=items;state.error='';}
    }catch(e){if(revision===state.revision)state.error=e.message;}
    if(session===solveSessionId && step===problem?.steps[curStepIdx]?.id)paintAttachments();
  }
  async function uploadDiscussionFile(file) {
    if(!file || !solveSessionId || curStepIdx<0 || streaming || switching)return;
    const state=attachmentState(),session=solveSessionId,step=problem.steps[curStepIdx].id;
    if(state.pending)return;
    state.revision++;
    state.pending++;state.error='';state.retry=null;paintAttachments();
    try{const form=new FormData();form.append('file',file);
      const response=await fetch(attachmentBase(session,step),{method:'POST',body:form});
      const result=await response.json();if(!response.ok)throw new Error(result.detail || '上传失败，请重试');
      state.items.push(result);
    }catch(e){state.error=e.message;state.retry=file;}
    finally{state.pending--;if(session===solveSessionId && step===problem?.steps[curStepIdx]?.id)paintAttachments();}
  }
  let draftProfileId = 'guest';
  const localDrafts = new Map();
  const draftKey = (session, step) => 'mm_solve_draft_v1.' + JSON.stringify([draftProfileId, session, step]);
  function readDraft(key) {
    try { const record=JSON.parse(localStorage.getItem(key) || 'null');if(record && typeof record.text==='string')return record; } catch {}
    return localDrafts.get(key);
  }
  function writeDraft(key,text) {
    const previous=readDraft(key);
    const record=previous?.text===text ? previous : {text,revision:crypto.randomUUID()};
    localDrafts.set(key,record);
    try{localStorage.setItem(key,JSON.stringify(record));}catch{}
    return record;
  }
  function captureDraft() {
    const step=problem?.steps[curStepIdx];
    if(!step || !solveSessionId || restoring)return;
    inputDrafts[step.id]=$('sv-input').value;
    return writeDraft(draftKey(solveSessionId,step.id),$('sv-input').value);
  }
  function recordVisit(step) {
    if(!solveSessionId || document.hidden)return;
    try{
      const key='mm_research_visits_v1.'+draftProfileId;
      const stored=JSON.parse(localStorage.getItem(key) || '{}');
      const visits=stored && typeof stored==='object' && !Array.isArray(stored) ? stored : {};
      visits[solveSessionId]={at:Date.now(),stepId:step.id};
      localStorage.setItem(key,JSON.stringify(visits));
    }catch{}
  }
  let uiVersion = 0, initialThought = '', studentQuestion = '', statementText = '', isExample = false;
  let solveSessionId = null;       // 做题存档 id
  let historyProject = false;
  const stepConvs = {};            // step_id -> 该步的做题会话 id（恢复时接得上）

  // ── 门禁 ──
  Profile.require().then((p) => { if (p) {draftProfileId=p.id || 'guest';init();} });

  function init() {
    bindSetup();
    bindComposer();
    $("sv-edit-plan").onclick = () => { void openPlanEditor(); };
    const newReply=document.createElement('button');newReply.className='sv-btn new-reply';newReply.id='sv-new-reply';newReply.hidden=true;newReply.textContent='有新回复 ↓';$('sv-chat').after(newReply);newReply.onclick=()=>{scrollDownForce();newReply.hidden=true;};
    setupEditorBridge();
    setupSettings();
    setupModelMenu();
    loadSettings();
    setupLayout();
    SolveFlow.init({state:()=>({id:solveSessionId,problem,index:curStepIdx}),save:saveArchive,enter:enterStep,append:appendToInput,isBusy:()=>switching,layout:()=>window.IDE?.editor.refresh()});
    window.ModelingWorkflow?.init({append:appendToInput,send:text=>sendChat(text),save:saveArchive});
    $('sv-example').onclick=()=>{ $('sv-statement').value='示例项目（模拟数据）：从 A、B、C、D 四个候选站点选择两个救护站。四个区域人口权重为 50、30、15、5；各站点到四个区域的响应时间矩阵（分钟）为 [[4,7,12,14],[8,3,9,12],[12,8,4,7],[14,11,7,3]]。每个区域都必须在 8 分钟内到达，在此条件下最小化人口加权平均响应时间。';$('sv-thought').value='我想先选择 A 和 B，人口多的区域响应快，加权平均时间应该较短。但我不确定能否兼顾全部区域。';isExample=true;$('sv-example-note').textContent='示例项目 · 模拟数据；点击开始后才调用助教。';};
    if(new URLSearchParams(location.search).get('example')==='1')$('sv-example').click();
    const sid=new URLSearchParams(location.search).get('session_id');if(sid) restoreSession(sid);else loadArchive();
    $('workdir').addEventListener('change',()=>{if(problem && !restoring) saveArchive().catch(()=>{});});
    let codeTimer;window.IDE?.editor.on('change',()=>{if(!problem || switching || restoring || window.ModelingWorkflow?.isApplyingCode?.())return;clearTimeout(codeTimer);$('sv-save-state').textContent='未保存';const edited=problem;codeTimer=setTimeout(()=>{if(problem===edited && !restoring)saveArchive().catch(()=>{});},700);});
    window.addEventListener('beforeunload',e=>{if(switching || restoring || SolveFlow.isBusy() || SolveFlow.isDirty() || window.ModelingWorkflow?.isDirty() || $('sv-save-state').textContent!=='已保存' && problem){e.preventDefault();e.returnValue='';}});
    window.addEventListener('pagehide', () => {captureDraft();detachChatSubscription();});
    // A restored bfcache page needs a fresh subscription; generation belongs to the server.
    window.addEventListener('pageshow',e=>{if(e.persisted && problem && curStepIdx>=0)enterStep(curStepIdx);});
  }

  // 布局预设 + 面板显隐（layout.js）
  function setupLayout() { /* Version 2 uses task views; legacy layout preferences remain untouched. */ }

  // 编辑器 ⇄ 对话联动（编辑器/终端由 ide.js 提供：window.IDE）
  function appendToInput(text) {
    const input = $("sv-input");
    if (!input || input.disabled) return;
    const cur = input.value;
    input.value = (cur && !cur.endsWith("\n") ? cur + "\n\n" : cur) + text;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  }

  function setupEditorBridge() {
    // 选中编辑器代码 → 插入对话输入框
    const ins = $("btn-insert-sel");
    if (ins) ins.addEventListener("click", () => {
      const ed = window.IDE && window.IDE.editor;
      if (!ed) return;
      const sel = ed.getSelection() || ed.getValue();
      if (!sel.trim()) return;
      SolveFlow.view('discussion');
      appendToInput("```python\n" + sel + "\n```");
    });
    // 控制台折叠
    const tog = $("console-toggle");
    const con = $("ws-console");
    if (tog && con) tog.addEventListener("click", () => {
      con.classList.toggle("collapsed");
      tog.textContent = con.classList.contains("collapsed") ? "▴" : "▾";
      const ed = window.IDE && window.IDE.editor;
      if (ed) setTimeout(() => ed.refresh(), 50);
    });
    // AI 回复里的代码块 → 一键放进编辑器
    let t = null;
    const enhance = () => {
      clearTimeout(t);
      t = setTimeout(() => {
        $("sv-chat").querySelectorAll("pre").forEach((pre) => {
          if (pre.querySelector(".to-editor-btn")) return;
          const btn = document.createElement("button");
          btn.className = "to-editor-btn";
          btn.textContent = "→编辑器";
          btn.title = "把这段代码放进中间编辑器";
          btn.addEventListener("click", () => {
            const code = pre.querySelector("code");
            const text = code ? code.innerText : pre.innerText;
            const ed = window.IDE && window.IDE.editor;
            if (ed) { window.ModelingWorkflow?.proposeCode(text); }
          });
          pre.appendChild(btn);
        });
      }, 120);
    };
    new MutationObserver(enhance).observe($("sv-chat"), { childList: true, subtree: true });
  }

  // ========== 视图 A：立题（上传 / 粘贴 → 拆步） ==========
  function bindSetup() {
    const fileInput = $("sv-file");
    const chips = $("sv-chips");

    $("sv-upload").addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async (e) => {
      const f = e.target.files[0];
      e.target.value = "";
      if (!f) return;
      const chip = document.createElement("span");
      chip.className = "sv-chip";
      const label = document.createElement("span");
      label.textContent = "⏳ " + f.name;
      chip.appendChild(label);
      chips.appendChild(chip);
      try {
        const fd = new FormData();
        fd.append("file", f);
        if (runId) fd.append("conversation_id", runId);
        const r = await fetch("/api/upload", { method: "POST", body: fd });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          label.textContent = "✗ " + (err.detail || f.name); chip.classList.add("err");
          return;
        }
        const d = await r.json();
        runId = d.conversation_id;
        if (!files.includes(d.filename)) files.push(d.filename);
        label.textContent = "📎 " + d.filename;
        const del = document.createElement("button");
        del.className = "sv-chip-del"; del.title = "删除这个文件"; del.textContent = "×";
        del.addEventListener("click", async () => {
          const i = files.indexOf(d.filename);
          if (i >= 0) files.splice(i, 1);
          chip.remove();
          if (runId) {
            try {
              await fetch("/api/upload/remove", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ conversation_id: runId, filename: d.filename }),
              });
            } catch { /* 列表已移除即可 */ }
          }
        });
        chip.appendChild(del);
      } catch {
        label.textContent = "✗ " + f.name; chip.classList.add("err");
      }
    });

    $("sv-analyze").addEventListener("click", analyze);
    $("sv-reset").addEventListener("click", resetProblem);
  }

  async function analyze() {
    if(!$('workdir').value.trim() && !await chooseFolder()) return;
    if(window.ResearchUI && !await ResearchUI.needModel())return;
    const statement = ($("sv-statement").value || "").trim();
    const status = $("sv-status");
    if (!statement && !files.length) { status.textContent = "先粘贴题面或上传题目文件"; return; }
    initialThought=$('sv-thought').value.trim();studentQuestion=$('sv-question').value.trim();statementText=statement;
    const btn = $("sv-analyze");
    btn.disabled = true;
    const requestWatch=window.ChatTasks?.watch?.(status,{label:'题目拆解'});
    let analysisResult='未完成';
    status.textContent = "正在读题，把它拆成几个可以动手的小步骤…";
    window.ResearchCompanion?.notify('start',{id:'analyze',state:'reading',label:'读题与拆解'});
    try {
      const r = await fetch("/api/build/problems/analyze", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statement, initial_thought:initialThought, student_question:studentQuestion, run_id: runId || "", files }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        status.textContent = typeof err.detail === 'string' ? err.detail : "题目拆解暂时未完成，输入和附件仍保留。请稍后重试，或打开下方已有存档。";
        window.ResearchCompanion?.notify('result',{state:'offline',alpha:'题目尚未拆解完成，请查看题目下方的错误提示。'});
        return;
      }
      problem = await r.json();
      analysisResult='已完成';requestWatch?.finish(analysisResult);
      solveSessionId = null; historyProject=false; uiVersion=0; SolveFlow.clear();
      runId=runId || ('solve_' + crypto.randomUUID().replaceAll('-','').slice(0,12));
      Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
      renderProblem();
      await saveArchive();
      $('workdir').value = SolveFlow.data.workdir || '';
      await enterStep(0);
      analysisResult='已完成';
      status.textContent = "";
    } catch (e) {
      status.textContent = "连接没有接上，题目还在输入框里。请重试：" + e.message;
      window.ResearchCompanion?.notify('result',{state:'offline'});
    } finally {
      requestWatch?.finish(analysisResult);
      window.ResearchCompanion?.notify('finish',{id:'analyze'});
      btn.disabled = false;
    }
  }

  async function resetProblem() {
    if(restoring || switching || SolveFlow.isBusy() || window.IDE?.isRunning()) return;
    if(problem) {try{await saveArchive();}catch(e){addError(e.message);return;}}
    detachChatSubscription();
    window.ModelingWorkflow?.detach();
    history.replaceState(null,'','/solve');
    runId=null;files.length=0;uiVersion=0;initialThought='';studentQuestion='';statementText='';isExample=false;
    $('workdir').value='';$('filename').value='main.py';window.IDE?.editor.setValue('');$('btn-clear').click();
    $('sv-statement').value='';$('sv-thought').value='';$('sv-question').value='';$('sv-chips').innerHTML='';$('sv-example-note').textContent='';$('sv-save-state').textContent='';$('sv-tabs').hidden=true;
    Object.keys(inputDrafts).forEach(k=>delete inputDrafts[k]);SolveFlow.clear();
    problem = null; problemConvId = null; curStepIdx = -1; seen.clear();
    solveSessionId = null; historyProject=false;
    Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
    if (window.MMAnim) MMAnim.transitionView($("sv-cockpit"), $("sv-setup"));
    else { $("sv-cockpit").hidden = true; $("sv-setup").hidden = false; }
    $("sv-reset").hidden = true;
    $("sv-cur-title").textContent = "";
    $("sv-chat").innerHTML = "";
    setComposerEnabled(false);
    $("sv-step-head").innerHTML = `<div class="sv-step-empty">从左边的路线里点开一步，开始和助教一起做。</div>`;
    loadArchive();
    await chooseFolder();
  }

  // ========== 做题存档（题目快照 + 进度 + 每步对话，刷新/下次进来可继续） ==========
  function saveArchive() {
    if (!problem) return Promise.resolve();
    const capturedProblem=problem;
    const snapshot={title:problem.title || '我的题目',problem,run_id:runId || '',files:[...files],
      progress:{seen:Array.from(seen),cur_step:curStepIdx},step_convs:{...stepConvs},
      initial_thought:initialThought,student_question:studentQuestion,statement:statementText,is_example:isExample,
      workdir:$('workdir').value || SolveFlow.data.workdir || '',
      editor_state:{code:window.IDE?.editor.getValue() || '',filename:$('filename').value},input_drafts:{...inputDrafts}};
    const previousId=solveSessionId;
    $('sv-save-state').textContent='保存中…';
    const task=saveQueue.catch(()=>{}).then(async()=>{
      const id=previousId || (problem===capturedProblem?solveSessionId:'');
      const r=await fetch('/api/solve/sessions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...snapshot,id:id || '',ui_version:uiVersion})});
      const d=await r.json();if(!r.ok) throw new Error(d.detail || '存档失败');
      if(problem===capturedProblem){solveSessionId=d.id;uiVersion=d.ui_version || 0;SolveFlow.accept(d);history.replaceState(null,'','/solve?session_id='+encodeURIComponent(d.id));$('sv-save-state').textContent='已保存';}
      return d;
    });
    saveQueue=task;
    task.catch(e=>{if(problem===capturedProblem){$('sv-save-state').textContent='保存失败 · 点击重试';$('sv-save-state').title=e.message;$('sv-save-state').onclick=()=>saveArchive().catch(()=>{});}});
    return task;
  }

  async function loadArchive() {
    const box = $("sv-archive");
    const list = $("sv-archive-list");
    if (!box || !list) return;
    try {
      const items = await (await fetch("/api/solve/sessions")).json();
      if (!Array.isArray(items) || !items.length) { box.hidden = true; return; }
      list.innerHTML = "";
      items.forEach((it) => list.appendChild(archiveRow(it)));
      box.hidden = false;
    } catch { box.hidden = true; }
  }

  function archiveRow(it) {
    const row = document.createElement("div");
    row.className = "sv-arch-item";
    const when = it.updated_at ? new Date(it.updated_at * 1000).toLocaleString("zh-CN", { hour12: false }) : "";
    row.innerHTML =
      `<button class="sv-arch-main" title="继续这道题">` +
        `<span class="sv-arch-title">${MMRender.escapeHtml(it.title || "我的题目")}</span>` +
        `<span class="sv-arch-meta">已确认 ${it.confirmed_count || 0}/${it.total_steps || 0} · ${MMRender.escapeHtml(when)}</span>` +
      `</button>` +
      `<button class="sv-arch-del" title="删除这份存档">×</button>`;
    row.querySelector(".sv-arch-main").addEventListener("click", () => restoreSession(it.id));
    row.querySelector(".sv-arch-del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("删除这份做题存档？该题的对话记录也会一并删除。")) return;
      try { await fetch("/api/solve/sessions/" + encodeURIComponent(it.id), { method: "DELETE" }); } catch { /* 忽略 */ }
      loadArchive();
    });
    return row;
  }

  async function restoreSession(id) {
    if(restoring || switching)return;
    restoring=true;
    try {
    // Flush edits before changing project identity; reading a saved project makes no writes.
    try { if(problem && (!historyProject || $('sv-save-state').textContent!=='已保存' || window.ModelingWorkflow?.isDirty()))await saveArchive();else await saveQueue; }
    catch { alert('当前项目尚未保存，请先重试保存，再打开另一份项目。');return; }
    if(!problem){
      $('sv-setup').hidden=true;$('sv-cockpit').hidden=false;$('sv-reset').hidden=false;
      $('sv-cur-title').textContent='历史项目';$('sv-step-head').textContent='正在读取历史记录…';
      $('sv-chat').innerHTML='';setComposerEnabled(false);
      const loadingUrl=new URL(location.href);loadingUrl.searchParams.set('session_id',id);history.replaceState(null,'',loadingUrl);
    }
    let data;
    try {
      const r = await fetch("/api/solve/sessions/" + encodeURIComponent(id));
      data = await r.json();
      if (!r.ok) throw new Error(data.detail || '暂时无法读取历史项目');
      if(data?.id!==id || !Array.isArray(data.problem?.steps) || !data.problem.steps.length)throw new Error('这份项目的题目信息不完整，原存档已保留。');
    } catch (e) { if(!problem)$('sv-step-head').textContent='历史记录暂未载入';historyError(e.message,()=>restoreSession(id));return; }
    const params=new URLSearchParams(location.search);
    if(params.get('session_id')!==id){params.delete('step_id');params.delete('view');}
    params.set('session_id',id);
    history.replaceState(null,'','/solve?'+params.toString());
    detachChatSubscription();
    window.ModelingWorkflow?.detach();
    SolveFlow.clear();
    solveSessionId = data.id; historyProject=true; uiVersion=data.ui_version || 0;
    initialThought=data.initial_thought || '';studentQuestion=data.student_question || '';statementText=data.statement || '';isExample=!!data.is_example;
    Object.keys(inputDrafts).forEach(key=>delete inputDrafts[key]);
    Object.assign(inputDrafts,data.input_drafts || {});
    $('workdir').value=data.workdir || '';
    window.IDE?.setWorkdir(data.workdir || '');
    window.IDE?.editor.setValue(data.editor_state?.code || '');
    $('filename').value=data.editor_state?.filename || 'main.py';
    problem = data.problem;
    runId = data.run_id || null;
    files.length = 0;
    (data.files || []).forEach((f) => files.push(f));
    Object.keys(stepConvs).forEach((k) => delete stepConvs[k]);
    Object.assign(stepConvs, data.step_convs || {});
    renderProblem();
    // 回填已看过的步骤标记
    seen.clear();
    ((data.progress || {}).seen || []).forEach((i) => seen.add(Number(i)));
    SolveFlow.accept(data);
    $('sv-save-state').textContent='已保存';
    paintRoadmap();
    const savedIdx=Number(data.progress?.cur_step);
    const linkedIdx=problem.steps.findIndex(s=>s.id===params.get('step_id'));
    const target=linkedIdx>=0?linkedIdx:Number.isInteger(savedIdx)&&savedIdx>=0&&savedIdx<problem.steps.length?savedIdx:0;
    await enterStep(target);
    if(['outcome','verification'].includes(params.get('view'))) SolveFlow.view(params.get('view'));
    if(params.get('verification_mode'))SolveFlow.verificationMode(params.get('verification_mode'));
    } finally { restoring=false; }
  }

  function historyError(message, retry) {
    addError(message);
    const button=document.createElement('button');button.className='sv-btn';button.textContent='重试读取历史';
    button.onclick=()=>{button.remove();retry();};$('sv-chat').appendChild(button);
  }

  // ========== 视图 B：解题驾驶舱（路线图 + 当前步共创） ==========
  function renderProblem() {
    curStepIdx = -1; problemConvId = null; seen.clear(); stepReadyShown.clear();
    if (window.MMAnim) MMAnim.transitionView($("sv-setup"), $("sv-cockpit"));
    else { $("sv-setup").hidden = true; $("sv-cockpit").hidden = false; }
    $('sv-tabs').hidden=false;
    $("sv-reset").hidden = false;
    $("sv-cur-title").textContent = problem.title || "我的题目";
    $("sv-cur-title").title = problem.title || "我的题目";
    $("sv-rail-title").textContent = problem.title || "我的题目";

    const bg = $("sv-bg");
    MMRender.renderMarkdown(bg, "## "+(problem.title||"我的题目")+"\n\n"+(statementText||problem.background||""));
    const bgToggle = $("sv-bg-toggle");
    const statementDialog=$('sv-statement-dialog');
    bgToggle.onclick = () => statementDialog.showModal();
    $('sv-statement-close').onclick=()=>statementDialog.close();
    statementDialog.onclick=e=>{if(e.target===statementDialog)statementDialog.close();};
    statementDialog.onclose=()=>bgToggle.focus();

    renderRoadmap();

    $("sv-chat").innerHTML = "";
    $("sv-step-head").innerHTML = `<div class="sv-step-empty">从左边的路线里点开一步，开始和助教一起做。</div>`;
    setComposerEnabled(false);
  }

  function renderRoadmap() {
    const wrap = $("sv-roadmap");
    wrap.innerHTML = "";
    const tasks = Array.isArray(problem.tasks) ? problem.tasks : [];
    const groups = [];
    (problem.steps || []).forEach((step, idx) => {
      const tid = step.task_id || (tasks[0] && tasks[0].id) || "T1";
      let group = groups.find((g) => g.id === tid);
      if (!group) {
        const meta = tasks.find((t) => t.id === tid) || {};
        group = { id: tid, title: meta.title || "", items: [] };
        groups.push(group);
      }
      group.items.push({ step, idx });
    });
    const showGroups = groups.length > 1;
    const addStep = (step, idx) => {
      const node = document.createElement("button");
      node.className = "sv-step press";
      node.dataset.idx = String(idx);
      const mlabel = MOD[step.modality] || "";
      node.innerHTML =
        `<span class="sv-step-num">${idx + 1}</span>` +
        `<span class="sv-step-body">` +
          `<span class="sv-step-title">${MMRender.escapeHtml(step.title || "第 " + (idx + 1) + " 步")}` +
            (mlabel ? ` <span class="sv-step-mod">${mlabel}</span>` : "") +
          `</span>` +
        `</span>`;
      if ((step.covers || []).length) {
        node.querySelector(".sv-step-body").insertAdjacentHTML("beforeend",
          `<span class="sv-step-covers">${step.covers.map((id) => MMRender.escapeHtml(String(id))).join(" · ")}</span>`);
      }
      node.addEventListener("click", () => enterStep(idx));
      wrap.appendChild(node);
    };
    groups.forEach((group) => {
      if (showGroups) {
        const head = document.createElement("div");
        head.className = "sv-task-group";
        head.textContent = group.title || "未命名情景";
        head.title = group.title || "";
        wrap.appendChild(head);
      }
      group.items.forEach(({ step, idx }) => addStep(step, idx));
    });
    if (window.MMAnim) MMAnim.reveal(wrap, { observe: false });
    paintRoadmap();
  }

  // ── 题面要求与责任步骤（开局对照表） ──
  function coverageRows() {
    if (Array.isArray(problem?.coverage) && problem.coverage.length) return problem.coverage;
    const reqs = Array.isArray(problem?.requirements) ? problem.requirements : [];
    const steps = problem?.steps || [];
    if (!reqs.length || !steps.length) return [];
    const multi = (problem?.tasks || []).length > 1;
    const labelOf = (idx) => {
      const step = steps[idx] || {};
      const task = (problem?.tasks || []).find((t) => t.id === step.task_id);
      const title = step.title || ("第 " + (idx + 1) + " 步");
      return (multi && task && task.title) ? `${task.title} · ${title}` : title;
    };
    return reqs.map((row) => {
      const indexes = steps.map((_, i) => i).filter((i) => (steps[i].covers || []).includes(row.id));
      return { ...row, step_indexes: indexes, step_labels: indexes.map(labelOf),
               covered: indexes.length > 0,
               assumed: indexes.length > 0 && indexes.every((i) => steps[i].covers_assumed) };
    });
  }

  function paintRoadmap() {
    $("sv-roadmap").querySelectorAll(".sv-step").forEach((n) => {
      const i = Number(n.dataset.idx);
      n.classList.toggle("cur", i === curStepIdx);
      n.classList.remove('seen');
    });
    window.SolveFlow?.paint();
  }

  // ========== 编辑建模路线（9 个模块：可排序 / 换模块 / 改标题与引导语） ==========
  let planStages = null;
  let forceKickoffStep = null;

  async function ensurePlanStages() {
    if (planStages) return planStages;
    const response = await fetch('/api/solve/framework');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || '模块清单暂未载入，请重试');
    planStages = data.stages || [];
    if (!planStages.length) throw new Error('模块清单为空，请检查服务是否正常');
    return planStages;
  }

  function applyPlanResult(result) {
    const data = result?.session;
    if (!data || data.id !== solveSessionId) return;
    const currentId = problem?.steps?.[curStepIdx]?.id;
    problem = data.problem;
    uiVersion = data.ui_version || uiVersion;
    Object.keys(stepConvs).forEach((key) => delete stepConvs[key]);
    Object.assign(stepConvs, data.step_convs || {});
    forceKickoffStep = (result.module_swapped || []).includes(currentId) ? currentId : null;
    renderProblem();
    SolveFlow.accept(data);
    paintRoadmap();
    const target = problem.steps.findIndex((step) => step.id === currentId);
    if (target >= 0) void enterStep(target);
  }

  async function openPlanEditor() {
    if (!problem?.steps?.length || switching) return;
    let stages;
    try { stages = await ensurePlanStages(); } catch (e) { addError(e.message); return; }
    const canonical = stages.map((s) => s.key);
    const stageName = (key) => (stages.find((s) => s.key === key) || {}).name || key;
    const requirements = coverageRows();
    const dialog = $('sv-plan-dialog');
    const list = $('sv-plan-list');
    const status = $('sv-plan-status');
    // 草稿按情景分组：主线（第一组）必须保留 9 个模块，其它情景组自由增减。
    const taskMeta = {};
    (problem.tasks || []).forEach((task) => { taskMeta[task.id] = { id: task.id, title: task.title || '' }; });
    const draft = problem.steps.map((step, index) => {
      const tid = step.task_id || ((problem.tasks || [])[0] || {}).id || 'T1';
      if (!taskMeta[tid]) taskMeta[tid] = { id: tid, title: '' };
      return {
        id: step.id || ('s' + (index + 1)),
        task_id: tid,
        stage_key: step.stage_key || canonical[index] || canonical[0],
        title: step.title || '',
        prompt: step.prompt || '',
        covers: Array.isArray(step.covers) ? [...step.covers] : [],
        original: step.stage_key,
        originalTitle: step.title || '',
        originalPrompt: step.prompt || '',
        originalCovers: Array.isArray(step.covers) ? [...step.covers] : [],
        isNew: false,
      };
    });
    const mainTaskId = draft.length ? draft[0].task_id : 'T1';
    if (!taskMeta[mainTaskId]) taskMeta[mainTaskId] = { id: mainTaskId, title: '' };
    let seq = 0;
    const newStepId = () => 'n_' + Date.now().toString(36) + (++seq);
    const groupIds = () => draft.map((item) => item.task_id)
      .filter((tid, i, all) => all.indexOf(tid) === i);
    const freeKeys = (tid) => {
      const used = new Set(draft.filter((item) => item.task_id === tid).map((item) => item.stage_key));
      return canonical.filter((key) => !used.has(key));
    };

    const planRow = (item, index) => {
      const row = document.createElement('div');
      row.className = 'plan-row';
      const num = document.createElement('span');
      num.className = 'plan-num';
      num.textContent = String(index + 1).padStart(2, '0');
      const body = document.createElement('div');
      body.className = 'plan-body';
      const select = document.createElement('select');
      select.className = 'plan-select';
      select.setAttribute('aria-label', `第 ${index + 1} 步模块`);
      stages.forEach((stage) => {
        const option = document.createElement('option');
        option.value = stage.key;
        option.textContent = stage.name;
        if (stage.key === item.stage_key) option.selected = true;
        select.append(option);
      });
      select.onchange = () => { item.stage_key = select.value; render(); };
      const title = document.createElement('input');
      title.type = 'text';
      title.className = 'plan-title';
      title.maxLength = 80;
      title.value = item.title;
      title.placeholder = '本步标题';
      title.setAttribute('aria-label', `第 ${index + 1} 步标题`);
      title.oninput = () => { item.title = title.value; };
      const guide = document.createElement('details');
      guide.className = 'plan-guide';
      const summary = document.createElement('summary');
      summary.textContent = item.prompt ? '改写引导语' : '补写引导语';
      guide.append(summary);
      const area = document.createElement('textarea');
      area.className = 'plan-prompt';
      area.rows = 3;
      area.value = item.prompt;
      area.setAttribute('aria-label', `第 ${index + 1} 步引导语`);
      area.oninput = () => { item.prompt = area.value; };
      guide.append(area);
      const coverBox = document.createElement('details');
      coverBox.className = 'plan-covers';
      const coverSummary = document.createElement('summary');
      const chosen = item.covers.filter((rid) => requirements.some((row) => row.id === rid));
      coverSummary.textContent = chosen.length ? `负责的要求（${chosen.length}）` : '负责的要求（未指定）';
      coverBox.append(coverSummary);
      if (requirements.length) {
        requirements.forEach((req) => {
          const label = document.createElement('label');
          label.className = 'plan-cover-item';
          const box = document.createElement('input');
          box.type = 'checkbox';
          box.checked = item.covers.includes(req.id);
          box.onchange = () => {
            item.covers = box.checked
              ? [...new Set([...item.covers, req.id])]
              : item.covers.filter((rid) => rid !== req.id);
            coverSummary.textContent = item.covers.length
              ? `负责的要求（${item.covers.length}）` : '负责的要求（未指定）';
            updateStatus();
          };
          const text = document.createElement('span');
          text.innerHTML = `<b>${MMRender.escapeHtml(String(req.id))}</b> ${MMRender.escapeHtml(String(req.text))}`;
          label.append(box, text);
          coverBox.append(label);
        });
      } else {
        const note = document.createElement('p');
        note.className = 'plan-note';
        note.textContent = '这道题没有要求清单（旧存档），这一项可以忽略。';
        coverBox.append(note);
      }
      body.append(select, title, guide, coverBox);
      const move = document.createElement('div');
      move.className = 'plan-move';
      const siblings = draft.filter((entry) => entry.task_id === item.task_id);
      const localIndex = siblings.indexOf(item);
      const up = document.createElement('button');
      up.type = 'button'; up.className = 'sv-btn'; up.textContent = '↑'; up.title = '同组内上移';
      up.disabled = localIndex <= 0;
      up.onclick = () => {
        const from = draft.indexOf(item), to = draft.indexOf(siblings[localIndex - 1]);
        draft.splice(from, 1); draft.splice(to, 0, item); render();
      };
      const down = document.createElement('button');
      down.type = 'button'; down.className = 'sv-btn'; down.textContent = '↓'; down.title = '同组内下移';
      down.disabled = localIndex < 0 || localIndex >= siblings.length - 1;
      down.onclick = () => {
        const from = draft.indexOf(item), to = draft.indexOf(siblings[localIndex + 1]);
        draft.splice(from, 1); draft.splice(to, 0, item); render();
      };
      move.append(up, down);
      row.append(num, body, move);
      return row;
    };

    const updateStatus = () => {
      const duplicated = groupIds().some((tid) => {
        const keys = draft.filter((item) => item.task_id === tid).map((item) => item.stage_key);
        return new Set(keys).size !== keys.length;
      });
      const mainKeys = new Set(draft.filter((item) => item.task_id === mainTaskId)
        .map((item) => item.stage_key));
      const mainOk = canonical.every((key) => mainKeys.has(key));
      const uncovered = requirements.filter((row) => !draft.some((item) => item.covers.includes(row.id)))
        .map((row) => row.id);
      const dirty = draft.some((item) => item.isNew || item.stage_key !== item.original
        || item.title !== item.originalTitle || item.prompt !== item.originalPrompt
        || item.covers.join(',') !== item.originalCovers.join(','));
      const notes = [];
      if (duplicated) notes.push('同一组里每个模块只能用一次。');
      if (!mainOk) notes.push('主线必须保留全部 9 个模块。');
      if (uncovered.length) notes.push(`还没人认领的要求：${uncovered.join('、')}（可以在下面勾选）。`);
      if (!notes.length && dirty) notes.push('已改动，保存后会要求重新确认受影响的步骤。');
      $('sv-plan-save').disabled = duplicated || !mainOk || !draft.length;
      status.className = 'plan-status' + ((duplicated || !mainOk) ? ' plan-error' : '');
      status.textContent = notes.join(' ');
    };

    const render = () => {
      list.replaceChildren();
      groupIds().forEach((tid) => {
        const meta = taskMeta[tid] || (taskMeta[tid] = { id: tid, title: '' });
        const items = draft.filter((item) => item.task_id === tid);
        const head = document.createElement('div');
        head.className = 'plan-task';
        const taskTitle = document.createElement('input');
        taskTitle.type = 'text';
        taskTitle.className = 'plan-task-title';
        taskTitle.maxLength = 60;
        taskTitle.value = meta.title;
        taskTitle.placeholder = tid === mainTaskId ? '主线（必须含全部 9 个模块）' : '情景名，例：情景A：三层别墅';
        taskTitle.oninput = () => { meta.title = taskTitle.value; };
        const count = document.createElement('span');
        count.className = 'plan-task-count';
        count.textContent = `${items.length} 步`;
        head.append(taskTitle, count);
        if (tid !== mainTaskId) {
          const add = document.createElement('button');
          add.type = 'button'; add.className = 'sv-btn'; add.textContent = '＋ 加一步';
          add.disabled = freeKeys(tid).length === 0;
          add.onclick = () => {
            const key = freeKeys(tid)[0];
            draft.push({ id: newStepId(), task_id: tid, stage_key: key, title: stageName(key), prompt: '',
                         covers: [], original: null, originalTitle: '', originalPrompt: '',
                         originalCovers: [], isNew: true });
            render();
          };
          const remove = document.createElement('button');
          remove.type = 'button'; remove.className = 'sv-btn'; remove.textContent = '删除本情景';
          remove.onclick = () => {
            if (!window.confirm('删除这个情景组的全部步骤？该组已确认的成果会一并移除。')) return;
            for (let i = draft.length - 1; i >= 0; i -= 1) {
              if (draft[i].task_id === tid) draft.splice(i, 1);
            }
            delete taskMeta[tid];
            render();
          };
          head.append(add, remove);
        }
        list.append(head);
        items.forEach((item) => list.appendChild(planRow(item, draft.indexOf(item))));
      });
      updateStatus();
    };

    $('sv-plan-default').onclick = () => {
      const ordered = [];
      groupIds().forEach((tid) => {
        const items = draft.filter((item) => item.task_id === tid)
          .sort((a, b) => canonical.indexOf(a.stage_key) - canonical.indexOf(b.stage_key));
        ordered.push(...items);
      });
      draft.length = 0; draft.push(...ordered); render();
    };
    $('sv-plan-add-task').onclick = () => {
      const tid = 'T' + Date.now().toString(36);
      taskMeta[tid] = { id: tid, title: '新情景' };
      ['build', 'solve', 'analyze'].forEach((key) => {
        draft.push({ id: newStepId(), task_id: tid, stage_key: key, title: stageName(key), prompt: '',
                     covers: [], original: null, originalTitle: '', originalPrompt: '',
                     originalCovers: [], isNew: true });
      });
      render();
      list.scrollTop = list.scrollHeight;
    };
    $('sv-plan-close').onclick = () => dialog.close();
    $('sv-plan-save').onclick = async () => {
      const save = $('sv-plan-save');
      save.disabled = true;
      status.className = 'plan-status';
      status.textContent = '正在保存路线…';
      try {
        if (!solveSessionId) await saveArchive();
        const response = await fetch('/api/solve/sessions/' + encodeURIComponent(solveSessionId) + '/plan', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            expected_revision: Number(SolveFlow.data?.revision || 0),
            steps: draft.map((item) => ({ id: item.id, stage_key: item.stage_key, title: item.title,
                                          prompt: item.prompt, covers: item.covers, task_id: item.task_id })),
            tasks: Object.values(taskMeta).map((meta) => ({ id: meta.id, title: meta.title })),
          }),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || '路线保存失败');
        dialog.close();
        applyPlanResult(result);
        if (result.changed?.length) $('sv-save-state').textContent = '路线已更新 · 受影响步骤需重新确认';
      } catch (e) {
        status.className = 'plan-status plan-error';
        status.textContent = e.message;
      } finally {
        save.disabled = false;
      }
    };
    render();
    dialog.showModal();
  }

  async function enterStep(idx) {
    if (!problem || switching || SolveFlow.isBusy() || window.IDE?.isRunning()) return;
    if(!problem.steps[idx])return;
    // 切步/离开当前视图时释放任何仍打开的完成弹窗，避免模态挡住后续点击。
    document.querySelectorAll('dialog.step-ready-dialog[open]').forEach(d=>{try{d.close();}catch{}});
    if(curStepIdx>=0){captureDraft();chatScroll[problem.steps[curStepIdx].id]=$('sv-chat').scrollTop;}
    switching=true;
    if(!solveSessionId){try{await saveArchive();}catch(e){switching=false;addError(e.message);return;}}
    detachChatSubscription();
    const step = problem.steps[idx];
    problemConvId = null;
    curStepIdx = idx;
    seen.add(idx);
    SolveFlow.changedStep();
    paintRoadmap();
    const localDraft=readDraft(draftKey(solveSessionId,step.id));
    if(localDraft)inputDrafts[step.id]=localDraft.text;
    $('sv-input').value=inputDrafts[step.id] || '';

    // 本步任务卡：目标结论 + 可验收清单（每步开头就能一眼看到要交什么）
    const mlabel = MOD[step.modality] || "";
    const spec = stepAcceptance(step);
    const taskMeta = (problem?.tasks || []).find((t) => t.id === step.task_id);
    const groupTag = (taskMeta && taskMeta.title && (problem?.tasks || []).length > 1)
      ? " · " + taskMeta.title : "";
    const myReqs = coverageRows().filter((row) => (row.step_indexes || []).includes(idx));
    const reqBlock = myReqs.length
      ? `<div class="sv-step-goal-sub">本步负责的题面要求</div><ul class="sv-step-req-list">` +
        myReqs.map((row) => `<li><span class="sv-req-id">${MMRender.escapeHtml(String(row.id))}</span>` +
          `<span>${MMRender.escapeHtml(String(row.text))}</span></li>`).join("") + `</ul>`
      : "";
    $("sv-step-head").innerHTML =
      `<div class="sv-step-tag">第 ${idx + 1} 步${groupTag}${mlabel ? " · " + mlabel : ""}</div>` +
      `<h3 class="sv-step-h">${MMRender.escapeHtml(step.title || "")}</h3>` +
      `<div class="sv-step-goal">` +
        `<div class="sv-step-goal-head">本步目标</div>` +
        `<p class="sv-step-goal-text">${MMRender.escapeHtml(spec.objective)}</p>` +
        `<div class="sv-step-goal-sub">完成本步必须满足</div>` +
        `<ul class="sv-step-checklist" id="sv-step-checklist">` +
          spec.checklist.map(item =>
            `<li class="sv-check" data-id="${MMRender.escapeHtml(String(item.id))}">` +
            `<span class="sv-check-mark" aria-hidden="true">○</span>` +
            `<span class="sv-check-text">${MMRender.escapeHtml(String(item.text))}</span></li>`
          ).join("") +
        `</ul>` +
        reqBlock +
        `<p class="sv-step-goal-tip">助教每轮会汇报验收状态；全部满足时可一键总结本步。</p>` +
      `</div>` +
      `<div class="sv-step-task panel-md" id="sv-step-task"></div>`;
    MMRender.renderMarkdown($("sv-step-task"), step.prompt || "");
    window.ModelingWorkflow?.attach({sessionId:solveSessionId,stepId:step.id,stepTitle:step.title,conversationId:stepConvs[step.id]});
    paintChecklist(window.ModelingWorkflow?.stepAcceptance?.());
    void window.ModelingWorkflow?.refresh?.();

    // 清空可视对话（后端会话仍保留前几步上下文，模型看得到）
    $("sv-chat").innerHTML = "";
    setComposerEnabled(false);

    try {
      const r = historyProject ? await fetch('/api/solve/sessions/'+encodeURIComponent(solveSessionId)+'/steps/'+encodeURIComponent(step.id)+'/conversation') : await fetch("/api/solve/enter", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          problem_id: problem.id, step_id: step.id, solve_session_id:solveSessionId,
          conversation_id: stepConvs[step.id] || "",
        }),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        historyError("历史回答未载入：" + (e.detail || r.status),()=>enterStep(idx));
        return;
      }
      const d = await r.json();
      recordVisit(step);
      problemConvId = d.conversation_id;
      window.ModelingWorkflow?.attach({sessionId:solveSessionId,stepId:step.id,stepTitle:step.title,conversationId:problemConvId});
      if(problemConvId)stepConvs[step.id] = problemConvId;
      if(!historyProject)await saveArchive();
      await window.ChatTasks.ready;
      let resumeTask=problemConvId ? await ChatTasks.find({conversationId:problemConvId,endpoint:'/api/solve/chat'}) : null;
      const messages=d.messages || [];
      const turnIndex=resumeTask ? messages.findIndex(m=>m.client_message_id===resumeTask.client_message_id) : -1;
      const source=turnIndex>=0 ? messages[turnIndex] : null;
      // A later saved student turn means this old task is already represented in history.
      if(resumeTask && turnIndex>=0 && messages.slice(turnIndex+1).some(m=>m.role==='user')){
        ChatTasks.acknowledge(resumeTask.id);resumeTask=null;
      }
      renderChatHistory(resumeTask && turnIndex>=0 ? messages.slice(0,turnIndex) : messages);
      setComposerEnabled(true);
      if(resumeTask){
        // Replay this turn alone, using its original identity; never POST it again.
        void sendChat(resumeTask.request?.message ?? source?.content ?? '',resumeTask.request?.origin || source?.origin || 'student',resumeTask);
        return;
      }
      if(messages.length)return;
      if(historyProject && forceKickoffStep !== step.id)return;
      forceKickoffStep = null;
      // 系统开场不冒充学生原创思路。
      void sendChat(d.kickoff || "我们开始这一步吧。", 'system_kickoff');
    } catch (e) {
      historyError("读取这一步出错：" + e.message,()=>enterStep(idx));
    } finally { switching=false;if(chatScroll[step.id]!=null)$("sv-chat").scrollTop=chatScroll[step.id];
      if($('sv-discussion-attachments')?.replaceChildren)void loadAttachments(); }
  }

  // ========== 对话（/api/solve/chat，与 /api/chat 同协议） ==========
  function bindComposer() {
    const input = $("sv-input");
    const send = $("sv-send");
    $('sv-discussion-attach').onclick=()=>$('sv-discussion-file').click();
    $('sv-discussion-file').onchange=e=>{const file=e.target.files[0];e.target.value='';void uploadDiscussionFile(file);};
    let draftTimer;
    input.addEventListener("input", () => {
      if(curStepIdx>=0 && !restoring){captureDraft();clearTimeout(draftTimer);$('sv-save-state').textContent='未保存';const edited=problem;draftTimer=setTimeout(()=>{if(problem===edited && !restoring)saveArchive().catch(()=>{});},600);}
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 160) + "px";
    });
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing && !streaming) {
        e.preventDefault();
        const v = input.value.trim();
        if (v) sendChat(v);
      }
    });
    send.addEventListener("click", () => {
      if (streaming) { stopChat(); return; }
      const v = input.value.trim();
      if (v) sendChat(v);
    });
  }

  function setComposerEnabled(on) {
    $("sv-input").disabled = !on;
    $("sv-send").disabled = !on;
    if (on) $("sv-input").focus();
  }

  function setStreaming(on) {
    streaming = on;
    const send = $("sv-send");
    send.textContent = on ? "■" : "➤";
    send.title = on ? "停止生成" : "发送";
    send.classList.toggle("stop", on);
    if($('sv-discussion-attachments')?.replaceChildren)paintAttachments();
  }

  function renderChatHistory(messages) {
    messages.forEach(m=>{
      if(m.role==='user' && m.origin!=='system_kickoff')addUserMsg(m.content || '');
      else if(m.role==='assistant'){
        (m.events || []).filter(e=>e.type==='tool_result' || e.type==='search').forEach(e=>renderToolCard(e.display,true));
        if(m.content){const b=addAssistantShell();b.classList.remove('cursor');MMRender.renderMarkdown(b,m.content);}
      }
    });
  }

  // ── 本步目标与验收清单 ──
  function stepAcceptance(step){
    const raw = Array.isArray(step?.checklist) ? step.checklist : [];
    const checklist = raw.map((item,i)=>{
      if(item && typeof item==='object')return {id:String(item.id||('c'+(i+1))),text:String(item.text||item.item||'')};
      return {id:'c'+(i+1),text:String(item||'')};
    }).filter(x=>x.text);
    return {objective:String(step?.objective || step?.deliverable || '完成本步要解决的问题'),checklist};
  }
  function currentStepId(){return problem?.steps?.[curStepIdx]?.id || '';}
  let pendingStepSummary=false;
  const stepReadyShown=new Set();
  function openOutcomeSummary(){
    SolveFlow.view('outcome');
    const btn=$('flow-summarize');
    if(btn)btn.click();
  }
  function paintChecklist(state){
    const list=$('sv-step-checklist');if(!list)return;
    const stepId=currentStepId();
    // 只套用当前步骤的状态，避免切换步骤时把上一步的 ✓ 带过来。
    if(!state || !stepId || (state.step_id && state.step_id!==stepId))return;
    const satisfied=(state.satisfied||[]).map(x=>String(x).trim()).filter(Boolean);
    const hit=text=>satisfied.some(s=>s===text||s.includes(text)||text.includes(s));
    list.querySelectorAll('.sv-check').forEach(li=>{
      const text=li.querySelector('.sv-check-text')?.textContent||'';
      const done=hit(text);
      li.classList.toggle('done',done);
      const mark=li.querySelector('.sv-check-mark');if(mark)mark.textContent=done?'✓':'○';
    });
  }
  function showStepReady(display){
    if(document.querySelector('.step-ready-dialog'))return;
    const restore=document.activeElement;
    const dialog=document.createElement('dialog');dialog.className='step-ready-dialog';
    const title=document.createElement('h3');title.textContent='恭喜你，本步已经完成了！';
    const lead=document.createElement('p');
    lead.textContent='本步验收清单已全部满足。请点击「总结本步」，把这一步的结论整理进项目的全文记忆。';
    dialog.append(title,lead);
    if(display.summary){const sum=document.createElement('p');sum.className='step-ready-summary';sum.textContent=display.summary;dialog.append(sum);}
    const items=(display.checklist||[]).map(x=>x.text||x).filter(Boolean);
    if(items.length){
      const ul=document.createElement('ul');ul.className='step-ready-list';
      items.forEach(t=>{const li=document.createElement('li');li.textContent=String(t);ul.append(li);});
      dialog.append(ul);
    }
    const actions=document.createElement('div');actions.className='step-ready-actions';
    const later=document.createElement('button');later.type='button';later.className='sv-btn';later.textContent='继续深入';later.onclick=()=>dialog.close();
    const summarize=document.createElement('button');summarize.type='button';summarize.className='sv-btn primary';summarize.textContent='总结本步';
    summarize.onclick=()=>{
      dialog.close();
      // 本轮讨论要等服务端落盘后才能整理，流式未结束时等收尾再跳转。
      if(streaming){pendingStepSummary=true;return;}
      openOutcomeSummary();
    };
    actions.append(later,summarize);dialog.append(actions);
    dialog.addEventListener('close',()=>{dialog.remove();if(restore?.isConnected)restore.focus?.();});
    document.body.append(dialog);dialog.showModal();
  }
  window.addEventListener('studio-workflow',e=>{
    const detail=e.detail||{};
    if(detail.stepId && detail.stepId!==currentStepId())return;
    paintChecklist(detail.acceptance);
  });

  // Detaching a view only stops its reader. Explicit Stop is the only cancellation path.
  function detachChatSubscription() {
    chatViewVersion += 1;
    const active=activeChatRequest;
    activeChatRequest=null;
    abort?.abort();abort=null;
    active?.release?.();
    const vitals=$('sv-chat').nextElementSibling;if(vitals?.classList.contains('request-vitals'))vitals.remove();
    setStreaming(false);
    svRemoveThinking();
  }

  async function stopChat() {
    const active=activeChatRequest;
    if(!active)return;
    if(!active.taskReady){active.onCancel();detachChatSubscription();return;}
    try {
      const task=active.task || await active.taskReady;
      if(!task)throw new Error('任务尚未提交');
      await ChatTasks.cancel(task);
      active.onCancel();
      active.controller.abort();
    } catch {
      if(activeChatRequest===active)addError('停止请求未送达，后台可能仍在运行。请恢复连接后重试。');
    }
  }

  async function sendChat(text, origin='student', resumeTask=null) {
    if(!resumeTask && attachmentState().pending){addError('附件正在上传，完成后再发送这条消息。');return;}
    if(streaming || (switching && origin==='student' && !resumeTask))return;
    if(!problemConvId && historyProject && origin==='student' && curStepIdx>=0){
      switching=true;setComposerEnabled(false);
      try{
        const step=problem.steps[curStepIdx];
        const r=await fetch('/api/solve/enter',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({problem_id:problem.id,step_id:step.id,solve_session_id:solveSessionId})});
        const d=await r.json();if(!r.ok)throw new Error(d.detail || '暂时无法继续这一步');
        problemConvId=d.conversation_id;stepConvs[step.id]=problemConvId;
        window.ModelingWorkflow?.attach({sessionId:solveSessionId,stepId:step.id,stepTitle:step.title,conversationId:problemConvId});
      }catch(e){$('sv-input').value=text;inputDrafts[problem.steps[curStepIdx].id]=text;addError(e.message);return;}
      finally{switching=false;setComposerEnabled(true);}
    }
    if(!problemConvId)return;
    const targetConversation=problemConvId, targetSession=solveSessionId;
    const targetStep=problem.steps[curStepIdx], version=chatViewVersion;
    const submittedDraft=!resumeTask && origin==='student' && $('sv-input').value.trim()===text ? captureDraft() : null;
    const submittedDraftKey=draftKey(targetSession,targetStep.id);
    const isRetry=!resumeTask && retryMessage?.conversation_id===targetConversation && retryMessage.text===text && retryMessage.origin===origin;
    const clientMessageId=resumeTask?.client_message_id || (isRetry?retryMessage.id:crypto.randomUUID());
    const priorNodes=isRetry && retryMessage.priorNodes ? retryMessage.priorNodes : new Set($('sv-chat').children);
    const controller=new AbortController();
    const companionTools=new Set(), companionId='chat-'+clientMessageId;
    let completed=false, explicitlyCancelled=false, terminalError=false, released=false;
    let requestWatch=null;
    let bubble=null, buf='', renderTimer=null, reader=null, paintedBubble=null, paintedText='';
    const active={conversation_id:targetConversation,client_message_id:clientMessageId,controller,task:resumeTask,
      onCancel:()=>{explicitlyCancelled=true;retryMessage=null;},
      release:()=>{
        if(released)return;released=true;
        if(!isCurrent())requestWatch?.dispose();
        for(const id of companionTools)window.ResearchCompanion?.notify('finish',{id,sessionId:targetSession});
        companionTools.clear();
        window.ResearchCompanion?.notify('finish',{id:companionId,sessionId:targetSession});
      }};
    const isCurrent=()=>chatViewVersion===version && activeChatRequest===active && problemConvId===targetConversation && solveSessionId===targetSession;
    const restoreInput=()=>{
      if(!isCurrent() || origin!=='student' || explicitlyCancelled)return;
      retryMessage={conversation_id:targetConversation,text,origin,id:clientMessageId,priorNodes};
      if(text && !$('sv-input').value){$('sv-input').value=text;$('sv-input').dispatchEvent(new Event('input',{bubbles:true}));}
    };
    const flushRender=()=>{
      clearTimeout(renderTimer);renderTimer=null;
      if(isCurrent() && bubble && (paintedBubble!==bubble || paintedText!==buf)){
        MMRender.renderMarkdown(bubble,buf);paintedBubble=bubble;paintedText=buf;scrollDown();
      }
    };
    const scheduleRender=()=>{if(renderTimer===null)renderTimer=setTimeout(flushRender,80);};
    // Capture editor/mode/project context before any asynchronous model check or navigation.
    const request={conversation_id:targetConversation,message:text,client_message_id:clientMessageId,origin,pid:draftProfileId,
      editor_context:window.ModelingWorkflow?.editorContext() || null,...(window.ModelingWorkflow?.chatContext() || {})};
    const taskUrl='/solve?session_id='+encodeURIComponent(targetSession)+'&step_id='+encodeURIComponent(targetStep.id)+'&view=discussion';
    const taskTitle=(problem.title || '我的题目')+' · '+targetStep.title;
    activeChatRequest=active;abort=controller;setStreaming(true);
    try {
      if(!resumeTask && window.ResearchUI && !await ResearchUI.needModel()){restoreInput();return;}
      await window.ChatTasks.ready;
      if(explicitlyCancelled)return;
      if(isCurrent()){
        if(!resumeTask){inputDrafts[targetStep.id]=$('sv-input').value;saveArchive().catch(()=>{});}
        if(isRetry)Array.from($('sv-chat').children).filter(node=>!priorNodes.has(node)).forEach(node=>node.remove());
        if(origin==='student')addUserMsg(text);
        svShowThinking();
        requestWatch=window.ChatTasks?.watch?.($('sv-chat'),{label:'助教回复',task:resumeTask});
        window.ResearchCompanion?.notify('start',{id:companionId,sessionId:targetSession,state:'thinking',label:'助教回复',cancel:()=>stopChat()});
      }
      active.taskReady=resumeTask ? Promise.resolve(resumeTask) : ChatTasks.submit('/api/solve/chat',request,{url:taskUrl,title:taskTitle});
      active.task=await active.taskReady;
      requestWatch?.setTask(active.task);
      // Acknowledgement, not clicking Send, transfers ownership of this text to the task.
      // Compare the saved revision so a late response cannot erase a newer draft or another view.
      if(submittedDraft && readDraft(submittedDraftKey)?.revision===submittedDraft.revision){
        writeDraft(submittedDraftKey,'');
        if(solveSessionId===targetSession && inputDrafts[targetStep.id]===submittedDraft.text)inputDrafts[targetStep.id]='';
        if(isCurrent() && $('sv-input').value===submittedDraft.text){$('sv-input').value='';$('sv-input').style.height='auto';saveArchive().catch(()=>{});}
      }
      if(!isCurrent())return;
      const resp=await ChatTasks.response(active.task,{signal:controller.signal,after:0});
      if(!resp.ok){
        let detail='请求失败';try{detail=(await resp.json()).detail || detail;}catch{}
        throw new Error(detail);
      }
      if(!resp.body)throw new Error('浏览器未能建立回复连接，请重试连接');
      reader=resp.body.getReader();
      const decoder=new TextDecoder();let sseBuf='';
      const handleEvent=ev=>{
        if(!isCurrent())return;
        switch(ev.type){
          case 'meta':
            window.ModelingWorkflow?.chatMeta(ev);
            break;
          case 'search':
            renderToolCard(ev.display);scrollDown();break;
          case 'task_wait': {
            const note=document.createElement('div');note.className='sv-wait-note';note.setAttribute('role','status');note.textContent=ev.message;
            $('sv-chat').appendChild(note);scrollDown();break;
          }
          case 'token':
            if(!bubble)bubble=addAssistantShell();
            buf+=ev.text || '';bubble.classList.add('cursor');scheduleRender();
            break;
          case 'tool_call':
            flushRender();
            if(ev.name==='run_python'){
              const id=companionId+'-tool-'+ev.call_id;companionTools.add(id);
              window.ResearchCompanion?.notify('start',{id,sessionId:targetSession,state:'running',label:'助教代码运行'});
            }
            svRemoveThinking();
            if(bubble){bubble.classList.remove('cursor');bubble=null;buf='';}
            break;
          case 'tool_result': {
            const id=companionId+'-tool-'+ev.call_id;
            window.ResearchCompanion?.notify('finish',{id,sessionId:targetSession});companionTools.delete(id);
            // Replayed file writes must not propose a second editor replacement.
            renderToolCard(ev.display,!!resumeTask);
            if(ev.display?.type==='step_ready'){
              paintChecklist(ev.display);
              const key=ev.display.step_id||currentStepId();
              if(ev.display.ready && !resumeTask && !stepReadyShown.has(key)){
                stepReadyShown.add(key);showStepReady(ev.display);
              }
            }
            scrollDown();break;
          }
          case 'done':
            completed=true;retryMessage=null;
            if(!bubble && ev.content)bubble=addAssistantShell();
            if(bubble){bubble.classList.remove('cursor');if(ev.content)buf=ev.content;}
            flushRender();
            break;
          case 'error':
            terminalError=true;flushRender();addError(ev.message || '出错了');restoreInput();
            retryMessage=null;
            window.ResearchCompanion?.notify('result',{state:'offline'});
            break;
          case 'cancelled':
            explicitlyCancelled=true;retryMessage=null;flushRender();
            if(bubble)bubble.classList.remove('cursor');
            break;
        }
      };
      const parsePart=part=>{
        const payload=part.split(/\r?\n/).filter(line=>line.startsWith('data:')).map(line=>line.slice(5).trimStart()).join('\n');
        if(!payload)return;
        let event;try{event=JSON.parse(payload);}catch{return;}handleEvent(event);
      };
      while(isCurrent()){
        const {done,value}=await reader.read();
        if(done)break;
        sseBuf+=decoder.decode(value,{stream:true});
        const parts=sseBuf.split(/\r?\n\r?\n/);sseBuf=parts.pop();parts.forEach(parsePart);
      }
      sseBuf+=decoder.decode();if(sseBuf.trim())parsePart(sseBuf);
      flushRender();
      if(isCurrent() && (completed || terminalError || explicitlyCancelled))ChatTasks.acknowledge(active.task.id);
    } catch(e) {
      if(isCurrent() && e?.name!=='AbortError'){
        restoreInput();addError('回复连接中断：'+e.message+'。切换回来可继续接收，后台任务仍会保留。');
        window.ResearchCompanion?.notify('result',{state:'offline'});
      }
    } finally {
      if(isCurrent())flushRender();
      clearTimeout(renderTimer);
      if(reader){reader.cancel().catch(()=>{});}
      active.release();
      if(isCurrent())requestWatch?.finish(explicitlyCancelled?'已停止':terminalError?'生成失败':completed?'已完成':'接收已断开，后台状态待确认');
      else requestWatch?.dispose();
      if(isCurrent()){
        if(!completed && !terminalError)restoreInput();
        setStreaming(false);abort=null;activeChatRequest=null;svRemoveThinking();
        if(bubble)bubble.classList.remove('cursor');
        if(pendingStepSummary){
          pendingStepSummary=false;
          if(completed)openOutcomeSummary();
        }
        // These refreshes only read the current project; generation never holds the view busy.
        void SolveFlow.refresh().catch(()=>{});
        void window.ModelingWorkflow?.refresh().catch(()=>{});
      }
    }
  }

  // ── 对话 DOM ──
  function addUserMsg(text) {
    const div = document.createElement("div");
    div.className = "sv-msg user anim-bubble";
    div.innerHTML = `<div class="sv-msg-role">我</div><div class="sv-bubble"></div>`;
    div.querySelector(".sv-bubble").textContent = text;
    $("sv-chat").appendChild(div);
    scrollDownForce();
  }

  function addAssistantShell() {
    svRemoveThinking();
    const div = document.createElement("div");
    div.className = "sv-msg assistant anim-bubble";
    div.innerHTML = `<div class="sv-msg-role">助教</div><div class="sv-bubble cursor"></div>`;
    $("sv-chat").appendChild(div);
    scrollDown();
    return div.querySelector(".sv-bubble");
  }

  // AI 思考三点占位
  let svThinking = null;
  function svShowThinking() {
    svRemoveThinking();
    if (!window.MMAnim) return;
    svThinking = document.createElement("div");
    svThinking.className = "sv-msg assistant";
    svThinking.innerHTML = `<div class="sv-msg-role">助教</div>`;
    svThinking.appendChild(MMAnim.thinkingEl());
    $("sv-chat").appendChild(svThinking);
    scrollDown();
  }
  function svRemoveThinking() {
    if (svThinking) { svThinking.remove(); svThinking = null; }
  }

  function addError(msg) {
    svRemoveThinking();
    const div = document.createElement("div");
    div.className = "sv-msg assistant";
    div.innerHTML = `<div class="sv-msg-role">助教</div><div class="sv-bubble"><p style="color:var(--rouge)">⚠ ${MMRender.escapeHtml(msg)}</p></div>`;
    $("sv-chat").appendChild(div);
    scrollDown();
  }

  // 「粘底」策略：仅当用户本来就在底部附近时，流式增量才自动滚到底；
  // 用户上滚阅读时不再打断（消除「AI 多打印一段就被拽到底」的问题）。
  let svAutoStick = true;
  (function bindSvScroll() {
    const c = $("sv-chat");
    if (c) c.addEventListener("scroll", () => {
      svAutoStick = c.scrollHeight - c.scrollTop - c.clientHeight < 80;
      if(svAutoStick && $('sv-new-reply'))$('sv-new-reply').hidden=true;
    });
  })();
  function scrollDown() {
    const c = $("sv-chat");
    if(c&&svAutoStick){c.scrollTop=c.scrollHeight;$("sv-new-reply").hidden=true;}else if(c)$("sv-new-reply").hidden=false;
  }
  function scrollDownForce() {
    const c = $("sv-chat");
    svAutoStick = true;
    if (c) c.scrollTop = c.scrollHeight;
  }

  // ── 工具卡片（紧凑版） ──
  function renderToolCard(display, restoring=false) {
    if (!display) return;
    const card = document.createElement("div");
    card.className = "tool-card";
    const esc = MMRender.escapeHtml;
    let label = "工具", cls = "ok", bodyHtml = "", open = false;

    if (display.type === "search") {
      if (display.out_of_scope) {
        cls = "err"; label = `检索「${esc(display.query)}」· 超出知识库范围`;
        bodyHtml = `<div class="out-scope">知识库中没有充分相关的内容，助教将谨慎补充。</div>`;
      } else {
        label = `检索知识库「${esc(display.query)}」· ${(display.citations || []).length} 个出处`;
        const chips = (display.citations || []).map((c) =>
          `<button class="cite" data-knowledge="${esc(String(c.chunk_id))}" title="查看引用出处">${esc(c.title || String(c.chunk_id))}</button>`
        ).join("");
        bodyHtml = `<div class="cite-list">${chips}</div>`;
      }
    } else if (display.type === 'workflow_wait') {
      const names={completed:'已完成',failed:'未完成',cancelled:'已取消',stale:'输入已变更',waiting_input:'等待补充材料',paused:'已暂停',interrupted:'已中断',stopped:'已停止等待',missing:'任务未找到'};
      label='后台验证 · '+esc(names[display.status] || display.status);
      cls=display.status==='completed'?'ok':'err';
      bodyHtml='<button type="button" class="sv-btn" data-open-results>查看验证记录</button>';
    } else if (display.type === "step_ready") {
      cls = display.ready ? "ok" : "err";
      label = display.ready ? "本步验收 · 全部满足" : "本步验收 · 还有待补";
      const done = (display.checklist || []).map((c) => `<span class="cite">${esc(c.text || c)}</span>`).join("");
      bodyHtml = (display.summary ? `<div class="out-scope">${esc(display.summary)}</div>` : "") +
        (done ? `<div class="cite-list">${done}</div>` : "") +
        (display.missing?.length ? `<div class="run-out stderr">还缺：${esc(display.missing.join("；"))}</div>` : "");
    } else if (display.type === "code_run") {
      cls = display.success ? "ok" : "err";
      const status = display.timed_out ? "超时" : display.success ? "成功" : "出错";
      label = `运行 Python 代码 · ${status}`; open = true;
      bodyHtml = `<pre><code class="language-python">${esc(display.code || "")}</code></pre>`;
      if (display.stdout) bodyHtml += `<div class="run-out">${esc(display.stdout)}</div>`;
      if (display.stderr) bodyHtml += `<div class="run-out stderr">${esc(display.stderr)}</div>`;
      for (const img of display.images || []) {
        bodyHtml += `<img class="run-img" src="data:image/png;base64,${img.data_base64}" alt="${esc(img.name || "")}" />`;
      }
    } else if (display.type === "document") {
      if (display.error) { cls = "err"; label = `读取文档「${esc(display.filename)}」· 失败`; bodyHtml = `<div class="run-out stderr">${esc(display.error)}</div>`; }
      else { label = `读取文档「${esc(display.filename)}」· ${display.chars} 字符`; bodyHtml = `<div class="out-scope">已读取文档内容并交给助教分析。</div>`; }
    } else if (display.type === "file_op") {
      const verbs = { write: "写入文件", read: "读取文件", list: "列出目录", delete: "删除文件" };
      cls = display.ok ? "ok" : "err";
      label = `${verbs[display.action] || "文件操作"}「${esc(display.path || "")}」· ${display.ok ? "成功" : "失败"}`;
      bodyHtml = display.ok ? `<div class="out-scope">${esc(display.action || "")} 完成。</div>` : `<div class="run-out stderr">${esc(display.error || "操作失败")}</div>`;
      // AI 写文件成功 → 通知中栏编辑器载入（由 editor-preview.js 监听）
      if (!restoring && display.action === "write" && display.ok && display.path) {
        window.ModelingWorkflow?.proposeFile(display.path);
      }
    } else {
      label = "工具：" + esc(display.type || "");
    }

    card.innerHTML =
      `<div class="tool-inner${open ? " open" : ""}">
        <div class="tool-head ${cls}"><span class="dot"></span><span class="label">${label}</span><span class="toggle">▸</span></div>
        <div class="tool-body">${bodyHtml}</div>
      </div>`;
    const inner = card.querySelector(".tool-inner");
    inner.querySelector(".tool-head").onclick = () => inner.classList.toggle("open");
    card.querySelectorAll("pre code").forEach((b) => { if (window.hljs) hljs.highlightElement(b); });
    card.querySelectorAll('[data-knowledge]').forEach(b=>b.onclick=()=>SolveFlow.openKnowledge(b.dataset.knowledge).catch(()=>addError('知识详情暂时无法打开')));
    card.querySelectorAll('[data-open-results]').forEach(b=>b.onclick=()=>window.ModelingWorkflow?.open('results'));
    $("sv-chat").appendChild(card);
  }

  // ========== 模型与 API 设置（与工作台同款，独立实现） ==========
  const PROVIDER_PRESETS = {
    gateway:  { base_url: "https://math-modeling.top/v1", model: "deepseek-v4-pro" },
    deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    qwen:     { base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus" },
    openai:   { base_url: "https://api.openai.com/v1", model: "gpt-4o-mini" },
    moonshot: { base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k" },
    custom:   { base_url: "", model: "" },
  };

  async function loadSettings() {
    try {
      const s = await (await fetch("/api/settings")).json();
      $("setting-api-key").value = s.api_key || "";
      $("setting-base-url").value = s.base_url || "";
      $("setting-model").value = s.model || "";
      $("model-badge").textContent = s.model || "";
      const matched = Object.entries(PROVIDER_PRESETS).find(([_, p]) => p.base_url === s.base_url && p.model === s.model);
      markPreset(matched ? matched[0] : "custom");
    } catch { /* 忽略 */ }
  }

  function markPreset(provider) {
    document.querySelectorAll("#provider-presets .preset").forEach((b) => {
      b.classList.toggle("active", b.dataset.provider === provider);
    });
    if (provider === "custom") return;
    const p = PROVIDER_PRESETS[provider];
    if (p && p.base_url) $("setting-base-url").value = p.base_url;
    if (p && p.model) $("setting-model").value = p.model;
  }

  function setupSettings() {
    document.querySelectorAll("#provider-presets .preset").forEach((btn) => {
      btn.addEventListener("click", () => {
        const provider = btn.dataset.provider;
        markPreset(provider);
        if (provider !== "custom") {
          const p = PROVIDER_PRESETS[provider];
          $("setting-base-url").value = p.base_url || "";
          $("setting-model").value = p.model || "";
        }
      });
    });
    $("btn-settings").onclick = () => { $("settings-overlay").classList.add("open"); loadSettings(); };
    $("btn-close-settings").onclick = () => $("settings-overlay").classList.remove("open");
    $("settings-overlay").addEventListener("click", (e) => {
      if (e.target === $("settings-overlay")) $("settings-overlay").classList.remove("open");
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { $("settings-overlay").classList.remove("open"); $("model-menu").hidden = true; }
    });
    $("btn-save-settings").onclick = async () => {
      const statusEl = $("settings-status");
      statusEl.className = "settings-status";

      const apiKey = $("setting-api-key").value.trim();
      if (apiKey && apiKey.includes("****")) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "请填入真实的 API Key（当前为掩码占位符，未实际修改）";
        $("setting-api-key").focus();
        return;
      }
      if (apiKey && apiKey.length < 15) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "API Key 太短，请检查是否完整粘贴";
        $("setting-api-key").focus();
        return;
      }

      statusEl.textContent = "保存中…";
      try {
        const resp = await fetch("/api/settings", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            api_key: $("setting-api-key").value,
            base_url: $("setting-base-url").value,
            model: $("setting-model").value,
          }),
        });
        if (!resp.ok) { const err = await resp.json().catch(() => ({})); throw new Error(err.detail || resp.statusText); }
        const result = await resp.json();
        $("model-badge").textContent = result.model || "";
        statusEl.className = "settings-status ok";
        statusEl.textContent = "设置已保存，模型已切换";
        setTimeout(() => { $("settings-overlay").classList.remove("open"); statusEl.textContent = ""; }, 1200);
      } catch (e) {
        statusEl.className = "settings-status err";
        statusEl.textContent = "保存失败：" + e.message;
      }
    };
  }

  function setupModelMenu() {
    const pill = $("model-pill");
    const menu = $("model-menu");
    const selector = $("model-selector");
    if (!pill || !menu || !selector) return;
    const closeMenu = () => { menu.hidden = true; };

    function markActive() {
      const cur = ($("model-badge").textContent || "").trim();
      menu.querySelectorAll(".model-opt[data-provider]").forEach((b) => {
        const p = PROVIDER_PRESETS[b.dataset.provider];
        b.classList.toggle("active", !!p && p.model === cur);
      });
    }

    pill.addEventListener("click", (e) => {
      e.stopPropagation();
      menu.hidden = !menu.hidden;
      if (!menu.hidden) markActive();
    });

    menu.querySelectorAll(".model-opt[data-provider]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const p = PROVIDER_PRESETS[btn.dataset.provider];
        if (!p) return;
        closeMenu();
        const badge = $("model-badge");
        const prev = badge.textContent;
        badge.textContent = p.model;
        $("setting-base-url").value = p.base_url;
        $("setting-model").value = p.model;
        markPreset(btn.dataset.provider);
        try {
          const resp = await fetch("/api/settings", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ base_url: p.base_url, model: p.model }),
          });
          if (!resp.ok) throw new Error();
          const r = await resp.json();
          badge.textContent = r.model || p.model;
        } catch {
          badge.textContent = prev;
          alert("切换模型失败，请在设置里检查 API 配置。");
        }
      });
    });

    const more = $("model-menu-more");
    if (more) more.addEventListener("click", () => { closeMenu(); $("settings-overlay").classList.add("open"); loadSettings(); });
    document.addEventListener("click", (e) => { if (!selector.contains(e.target)) closeMenu(); });
  }

  async function chooseFolder() {
    const dir = await FolderBrowser.open($('workdir').value.trim());
    if (!dir) return false;
    $('workdir').value = dir;
    $('workdir').dispatchEvent(new Event('change'));
    window.IDE?.setWorkdir(dir);
    return true;
  }
  const environmentButton = document.createElement('button');
  environmentButton.id = 'sv-work-environment';
  environmentButton.className = 'ide-btn';
  environmentButton.textContent = '工作环境';
  environmentButton.onclick = () => chooseFolder();
  $('sv-editor-tools').appendChild(environmentButton);
})();
