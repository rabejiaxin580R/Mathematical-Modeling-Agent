/* Adds student decisions to the existing solve workbench. Owns no editor/chat instances. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = s => MMRender.escapeHtml(String(s == null ? '' : s));
  const statusLabels = {confirmed:'已确认',needs_review:'需重新确认',draft:'待确认'};
  const verdicts = {pending:'尚未判断',supported:'支持我的想法',partial:'部分支持',unsupported:'不支持我的想法',insufficient:'证据不足'};
  const sources = {ai_run:'助教代码运行',local_run:'本机代码运行',user:'用户提供',theory:'理论推导',simulation:'模拟实验'};
  let api, data = {}, view = 'discussion', drafts = {}, evidenceDrafts = {}, busy = false;
  let focusBeforeReader;
  const editing={}, viewScroll={}, purposes={};
  // 中栏的显示切换现在只由任务 tab（代码/结果/笔记/论文草稿）负责；
  // 这里只保留旧链接 ?verification_mode=… 的兼容映射。
  const onResultsPanel=()=>document.body.dataset.workflowPanel==='results';
  const draftRevisions = {};
  const flow = {
    get data() { return data; },
    init(adapter) {
      api = adapter;
    // 中栏只有一套切换器（任务 tab）。编辑器、实验记录、终端都是「内容」，
    // 不再各自带一层开关：代码 tab 看编辑器，结果 tab 看记录与终端。
    const mid=document.querySelector('.sv-col-mid'),content=document.createElement('div');content.className='verify-content';
    const editor=mid.querySelector('.ws-editor'),terminal=$('ws-console'),purpose=mid.querySelector('.flow-purpose');
    content.append(purpose,editor,$('sv-evidence'),terminal);mid.append(content);
    const runState=document.createElement('span');runState.id='verify-run-state';runState.className='verify-run-state';
    runState.setAttribute('role','status');runState.textContent='就绪';
    $('sv-editor-tools').append(runState);
    const newResults=document.createElement('button');newResults.type='button';newResults.className='sv-btn';newResults.id='verify-new-results';
    newResults.hidden=true;newResults.textContent='有新的结果 ↓';mid.prepend(newResults);
    newResults.onclick=()=>{window.ModelingWorkflow?.open?.('results');renderEvidence();$('sv-evidence').scrollTop=0;content.scrollTop=0;newResults.hidden=true;};
    const runStatus=$('con-status');new MutationObserver(()=>{runState.textContent=runStatus.textContent;}).observe(runStatus,{childList:true,characterData:true,subtree:true});
    $('sv-purpose').oninput=()=>{const key=current().step?.id;if(key)purposes[key]=$('sv-purpose').value;};
    // 中栏内容的显示只由这一处决定：代码 tab 看编辑器，结果 tab 看记录与终端。
    // workflow-ui 的 setPanel 会调用它；引导预览页没有 workflow-ui，也用它兜底。
    const applyPanel=kind=>{
      if(purpose)purpose.hidden=kind!=='code';
      if(editor)editor.hidden=kind!=='code';
      if(terminal)terminal.hidden=kind!=='results';
      const evidence=$('sv-evidence');if(evidence)evidence.hidden=kind!=='results';
      const banner=$('verify-new-results');if(banner&&kind!=='results')banner.hidden=true;
    };
    flow.applyPanel=applyPanel;
    applyPanel('code');

      document.body.dataset.view = view;
      $('sv-tabs').querySelectorAll('[data-view]').forEach(b => b.onclick = () => flow.view(b.dataset.view));
      const draftEntry=document.querySelector('.sv-composer-hint');
      draftEntry.innerHTML='<button class="sv-btn" id="sv-draft-from-chat">去整理本步草稿</button>';
      $('sv-draft-from-chat').onclick=()=>{if(busy||api.isBusy())return;openDraftPage();};
      $('sv-route-toggle').onclick = () => {
        const open = document.body.classList.toggle('route-open');
        $('sv-route-toggle').setAttribute('aria-expanded', String(open));
        if(open) $('sv-roadmap').querySelector('.cur,button')?.focus();
      };
      document.addEventListener('keydown', e => { if (e.key === 'Escape' && document.body.classList.contains('route-open')) { document.body.classList.remove('route-open'); $('sv-route-toggle').setAttribute('aria-expanded','false'); $('sv-route-toggle').focus(); } });
      // 生成大纲不可逆，且未确认的步骤会交给 AI 直接补写；所以统一先给提醒再走。
      wirePaperWarning();
      $('sv-to-paper').onclick = () => {
        if (busy || api.isBusy()) return;
        const warning = $('solve-ai-warning');
        paintPaperWarning();
        if (warning && typeof warning.showModal === 'function') warning.showModal();
        else void generatePaper(true);
      };
      window.addEventListener('solve-evidence', async e => {
        const id = api.state().id;
        if (!id) return;
        try { const next = await request('/api/solve/sessions/' + encodeURIComponent(id)); if (api.state().id !== id) return; flow.accept(next); flow.paint(); if (onResultsPanel()) {const reading=$('sv-evidence').scrollTop>80||document.querySelector('.verify-content').scrollTop>80||!!$('sv-evidence').querySelector('details[open]');if(reading)$('verify-new-results').hidden=false;else renderEvidence();} }
        catch(e) { if (api.state().id === id) $('sv-save-state').textContent = '验证记录读取失败，请重试'; }
      });
      window.addEventListener('research-companion-action', async e => {
        if (!e.detail || busy || api.isBusy()) return;
        if (e.detail.stepId && e.detail.stepId !== current().step?.id) {
          const index = current().problem?.steps.findIndex(s => s.id === e.detail.stepId);
          if(index<0)return;
          await api.enter(index);
          if(current().step?.id!==e.detail.stepId)return;
        }
        flow.view(e.detail.view);
        const target = $(e.detail.view === 'verification' ? 'sv-evidence' : 'sv-outcome');
        if(target){target.tabIndex=-1;target.focus({preventScroll:true});}
      });
    },
    clear() { data = {}; drafts = {}; evidenceDrafts = {}; [draftRevisions,editing,viewScroll,purposes].forEach(map=>Object.keys(map).forEach(k=>delete map[k])); window.ResearchCompanion?.notify('reset'); flow.view('discussion'); },
    accept(next) { data = next || {}; flow.paint(); },
    async refresh() {
      const id = api.state().id;
      if (!id) return;
      const next = await request('/api/solve/sessions/' + encodeURIComponent(id));
      if (api.state().id !== id) return;
      flow.accept(next);
    if (onResultsPanel()) {
        const reading=$('sv-evidence').scrollTop>80 || document.querySelector('.verify-content').scrollTop>80 || !!$('sv-evidence').querySelector('details[open]');
        if(reading)$('verify-new-results').hidden=false;else renderEvidence();
      }
    },
    paint() {
      if (!api) return;
      const {problem,index} = api.state();
      if (!problem) return;
      const steps = problem.steps || [];
      if(data.id === api.state().id)window.ResearchCompanion?.sync({...data,currentStepId:steps[index]?.id});
      const count = steps.filter(s => data.outcomes?.[s.id]?.status === 'confirmed').length;
      $('sv-confirm-progress').textContent = `已确认 ${count} / ${steps.length}`;
      // 随时都能整理成论文（点击后先给「AI 重复度高」的提醒）；未确认步骤只列为待补充。
      $('sv-to-paper').disabled = busy || !steps.length;
      $('sv-to-paper').classList.toggle('primary', count === steps.length);
      if (!busy) $('sv-material-status').textContent = count === steps.length
        ? '所有步骤已确认。将采用的决定、依据和限制带入论文。'
        : `还有 ${steps.length - count} 步未确认；现在整理只会写入已确认的 ${count} 步，其余列为待补充。`;
      $('sv-roadmap').querySelectorAll('.sv-step').forEach(n => {
        const i = Number(n.dataset.idx), outcome = data.outcomes?.[steps[i]?.id];
        n.classList.remove('seen'); n.classList.toggle('confirmed',outcome?.status === 'confirmed');
        let label=n.querySelector('.flow-step-state');
        if (!label) { label=document.createElement('span'); label.className='flow-step-state'; n.querySelector('.sv-step-body').append(label); }
        label.textContent=statusLabels[outcome?.status] || (i === index?'讨论中':'未开始');
        n.querySelector('.sv-step-num').textContent=outcome?.status === 'confirmed'?'✓':String(i+1);
      });
      const adopted = steps.slice(0,index+1).map(s=>data.outcomes?.[s.id]).filter(o=>o?.status === 'confirmed');
      $('sv-current-thought').textContent = adopted.at(-1)?.decision || data.initial_thought || '还没想好，先和助教形成初步思路。';
      $('sv-original-thought').textContent = data.initial_thought || '开始时尚未形成思路。';
      if($('studio-stage-title')){
        const step=steps[index];
        $('studio-stage-number').textContent=String(index+1).padStart(2,'0');
        $('studio-stage-title').textContent=step?.title||'开始研究';
        const actions={restate:'先说说你的理解，找出真正需要解决的问题。',assume:'哪些条件可以简化，哪些必须保留？',notation:'给关键量取个名字，让思路更清楚。',build:'把你的想法写成可以检验的关系。',solve:'试着算一遍，看看想法能否走得通。',analyze:'一起检查结果，看看它说明了什么。',sensitivity:'改变一点条件，观察结论是否稳定。',evaluate:'说清模型的长处，也留下它的边界。',extend:'这份发现，还能用在哪里？'};
        $('studio-stage-action').textContent=actions[step?.stage_key]||'先说一个想法，我们一起推敲，再用依据验证。';
      }
    },
    changedStep() {
      document.body.classList.remove('route-open'); $('sv-route-toggle').setAttribute('aria-expanded','false');
      flow.paint(); flow.view('discussion');
      $('sv-purpose').value=purposes[current().step?.id]||'';
    },
    view(name) {
      if (!api) return;
      if (busy || (api.isBusy() && name === 'outcome')) return;
      if(!['discussion','verification','outcome'].includes(name))return;
      viewScroll[current().step?.id+':'+view]={chat:$('sv-chat').scrollTop,outcome:$('sv-outcome').scrollTop,evidence:$('sv-evidence').scrollTop};
      view = name; document.body.dataset.view = name; document.body.dataset.result = 'false';
      if(current().id && current().step){const url=new URL(location.href);url.searchParams.set('view',name);url.searchParams.set('step_id',current().step.id);history.replaceState(null,'',url);}
      $('sv-tabs').querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view === name); b.setAttribute('aria-pressed',String(b.dataset.view === name));});
      $('sv-outcome').hidden = name !== 'outcome'; $('sv-evidence').hidden = name !== 'verification';
      api.layout(name);
      if (name === 'outcome') renderOutcome();
      if (name === 'verification') renderEvidence();
      window.ModelingWorkflow?.onFlowView(name);
      window.IDE?.editor.refresh();
      const previous=viewScroll[current().step?.id+":"+name];if(previous){$("sv-chat").scrollTop=previous.chat;$("sv-outcome").scrollTop=previous.outcome;$("sv-evidence").scrollTop=previous.evidence;}
    },
    // 旧链接兼容：?verification_mode=notebook|code → 打开「结果」或「代码」tab。
    verificationMode(mode) {
      const kind = mode === 'code' ? 'code' : 'results';
      delete document.body.dataset.verificationMode;  // 旧的模式开关已废弃
      if (window.ModelingWorkflow?.open) window.ModelingWorkflow.open(kind, false);
      else flow.applyPanel?.(kind);
    },
    runContext() {
      const s=api?.state(); if (!s?.id || s.index < 0) return null;
      return {solve_session_id:s.id,step_id:s.problem.steps[s.index].id,purpose:$('sv-purpose').value.trim() || s.problem.steps[s.index].title};
    },
    openEvidence(recordId) {
      const e=(data.evidence||[]).find(r=>r.id===recordId);if(!e)return;const sid=current().id;
      const dialog=document.createElement('dialog');dialog.className='flow-history-dialog';const origin=document.activeElement;
      dialog.innerHTML='<button class="ui-btn" autofocus>关闭</button><h3>'+esc(e.purpose)+'</h3><p class="flow-note">'+esc(sources[e.source]||e.source)+' · '+new Date(e.created_at*1000).toLocaleString('zh-CN')+'</p><p>来源步骤：'+esc(data.problem?.steps?.find(s=>s.id===e.step_id)?.title||e.step_id)+' · 成果版本 '+(e.outcome_version||0)+'</p><pre>'+esc(e.stdout||e.stderr||'没有文字输出')+'</pre>'+(e.images||[]).map(img=>'<img style="max-width:100%" src="/api/solve/sessions/'+encodeURIComponent(sid)+'/assets/'+encodeURIComponent(img.asset)+'" alt="'+esc(img.name)+'">').join('')+'<details><summary>查看归档代码（只读）</summary><pre>'+esc(e.code||'无代码')+'</pre></details>';
      dialog.querySelector('button').onclick=()=>dialog.close();dialog.onclose=()=>{dialog.remove();origin?.focus();};dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});document.body.append(dialog);dialog.showModal();
    },
    ask(text) { flow.view('discussion'); api.append(text); },
    currentMaterial() {
      if(!api)return null;
      const {id,step,index,problem}=current();if(!id||!step)return null;
      const outcome=data.outcomes?.[step.id]||{};
      return {sessionId:id,stepId:step.id,title:step.title,index,count:problem.steps.length,confirmed:outcome.status==='confirmed',status:outcome.status||'draft',decision:outcome.decision||'',reason:outcome.reason||'',basis:outcome.basis||'',limits:outcome.limits||'',initialThought:data.initial_thought||'',projectTitle:problem.title||'',version:outcome.version||0};
    },
    isBusy: () => busy,
    isDirty: () => Object.keys(drafts).length > 0 || Object.keys(evidenceDrafts).length > 0,
    async openKnowledge(id) {
      focusBeforeReader = document.activeElement;
      if (!window.MMKnowledgeReader) {
        const html = await (await fetch('/learn')).text();
        const doc = new DOMParser().parseFromString(html,'text/html');
        const reader = doc.getElementById('reader');
        if (!reader) return;
        document.body.appendChild(reader);
        await new Promise((resolve,reject)=>{const script=document.createElement('script');script.src='/static/learn.js?v=research3';script.onload=resolve;script.onerror=reject;document.body.appendChild(script);});
      }
      MMKnowledgeReader.mount({onAsk:q=>{MMKnowledgeReader.close();flow.ask(q);},onClose:()=>focusBeforeReader?.focus()});
      MMKnowledgeReader.open(id);
    },
  };
  async function request(url, body) {
    const res = await fetch(url, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : {});
    const result = await res.json();
    if (!res.ok) throw new Error(typeof result.detail === 'string' ? result.detail : '操作失败，请重试');
    return result;
  }
  function current() { const s=api.state(); return { ...s, step:s.problem?.steps[s.index] }; }
  // 草稿整理搬到专属论文区域：这里只负责带参数跳转，不在聊天界面内执行。
  function openDraftPage() {
    const sessionId = api?.state?.().id, stepId = current().step?.id;
    if (!sessionId || !stepId) { $('sv-save-state').textContent = '请先进入一个建模步骤'; return; }
    location.href = '/draft?session_id=' + encodeURIComponent(sessionId) + '&step_id=' + encodeURIComponent(stepId);
  }
  // 开始整理论文：先建好论文会话，再带着内容跳到大纲页并自动开始生成大纲。
  // 未确认的步骤由 AI 直接补写，所以入口必须走「不可逆 + AI 重复度高」的提醒。
  async function generatePaper(allowPartial) {
    if (busy || api.isBusy()) return;
    busy = true; $('sv-to-paper').disabled = true;
    let failure = '';
    try {
      await api.save();
      // 注意：本文件的 request(url, body) 只接两个参数，第二个就是请求体。
      const created = await request('/api/paper/create',
        { solve_session_id: api.state().id, allow_partial: !!allowPartial });
      const pid = created?.paper_session_id;
      if (pid) {
        // 大纲页自己开始生成；学生在大纲页查看、修改后，再进入完整论文。
        location.href = '/outline?session_id=' + encodeURIComponent(pid) + '&autostart=1';
        return;
      }
      failure = '暂时无法创建论文会话，请稍后重试。';
    } catch (e) { failure = e.message; }
    finally { busy = false; flow.paint(); if (failure) $('sv-material-status').textContent = failure; }
  }
  function wirePaperWarning() {
    const warning = $('solve-ai-warning');
    if (!warning) return;
    $('saw-back').onclick = () => warning.close();
    warning.addEventListener('click', e => { if (e.target === warning) warning.close(); });
    $('saw-review').onclick = () => { warning.close(); flow.view('outcome'); };
    $('saw-continue').onclick = () => { warning.close(); void generatePaper(true); };
  }
  // 提醒文案随确认进度变化：有未确认步骤就说明「AI 会直接替你写」。
  function paintPaperWarning() {
    const lead = $('saw-lead');
    if (!lead) return;
    const steps = api?.state?.().problem?.steps || [];
    const confirmed = steps.filter(s => data.outcomes?.[s.id]?.status === 'confirmed').length;
    const missing = Math.max(0, steps.length - confirmed);
    if (missing) {
      lead.innerHTML = `还有 <b>${missing}</b> 步没有确认——这些内容会由 <b>AI 直接替你写</b>。`
        + 'AI 生成的内容千篇一律、重复性很高，评阅时很容易被看出雷同，'
        + '<b>完全依赖 AI 会大幅降低获奖的可能性</b>。竞赛允许使用生成式 AI，'
        + '但想拿奖，最关键的还是你自己的想法。';
    } else {
      lead.innerHTML = '9 步已经全部由你自己确认，论文会按你的结论整理。'
        + '仍然提醒一句：<b>这一步不可逆</b>，生成大纲后想回头只能自己手动说明。';
    }
  }
  function renderOutcome() {
    const {step,id,index,problem}=current(); const wrap=$('sv-outcome');
    if (!step) { wrap.innerHTML='<p>先选择一个建模步骤。</p>'; return; }
    const o=data.outcomes?.[step.id] || {}, draft=drafts[step.id] || {...o};
    if (!drafts[step.id]) draftRevisions[step.id] = data.revision || 0;
    const records=(data.evidence || []);
    wrap.innerHTML=`<div><h3>本步成果</h3>
      <p class="flow-note ${o.status==='needs_review'?'warning':''}">${esc(statusLabels[o.status] || '尚未确认')} · 成果第 ${o.version || 0} 版${o.status==='needs_review'?' · 前面的决定已修改，请检查本步是否仍成立。':''}</p>
      <button id="flow-summarize" class="sv-btn primary">从讨论中整理草稿</button>
      <div id="flow-edit-fields"><label>本步决定<textarea data-field="decision" placeholder="我决定采用……">${esc(draft.decision)}</textarea></label>
      <label>采用的理由<textarea data-field="reason" placeholder="因为……；如本步不适用，请说明原因。">${esc(draft.reason)}</textarea></label>
      <label>已有依据<textarea data-field="basis" placeholder="知识出处、公式推导、外部材料或结果解释">${esc(draft.basis)}</textarea></label>
      <label>限制与待办<textarea data-field="limits" placeholder="还不能说明什么？适用条件是什么？">${esc(draft.limits)}</textarea></label>
      <label>结果对我的想法意味着什么<select data-field="verdict">${Object.entries(verdicts).map(([k,v])=>`<option value="${k}" ${draft.verdict===k?'selected':''}>${v}</option>`).join('')}</select></label>
      <label><input data-field="not_applicable" type="checkbox" ${draft.not_applicable?'checked':''}>本步骤不适用（上方填写理由）</label>
      <div class="flow-evidence-check"><label>引用验证记录</label>${records.length?records.map(e=>`<label><input type="checkbox" data-evidence="${esc(e.id)}" ${(draft.evidence_ids || []).includes(e.id)?'checked':''}>${esc(e.purpose)} · ${esc(sources[e.source])} · ${e.status==='failed'?'运行失败':e.status==='success'?'已运行':'已提供'}</label>`).join(''):'<p class="flow-note">尚无验证记录。可在“计算与验证”运行代码、记录推导或提供外部结果。</p>'}</div>
      <p class="flow-note">修改已确认内容会保留旧版，并使后续成果需要重新确认。</p>
      </div><div class="flow-actions"><button id="flow-save" class="sv-btn">保存草稿</button><button id="flow-confirm" class="sv-btn primary">${o.status==='confirmed'?'更新并确认本步':'确认本步成果'}</button>${index<problem.steps.length-1?'<button id="flow-next" class="sv-btn" '+(o.status==='confirmed'?'':'disabled')+'>进入下一步 →</button>':''}</div>
      <p id="flow-outcome-status" role="status" class="flow-note"></p>
      <button id="flow-reload" class="sv-btn" hidden>读取最新成果，保留我的草稿</button>
      ${(o.history || []).length?`<details class="flow-record"><summary>查看已确认的历史版本</summary>${o.history.map(h=>`<div><b>第 ${h.version} 版</b><p>${esc(h.decision)}</p><p class="flow-note">${esc(h.basis)}</p></div>`).join('')}</details>`:''}
      ${index===problem.steps.length-1?'<details class="flow-record"><summary>写作材料汇总</summary>'+problem.steps.map(s=>`<p><b>${esc(s.title)}</b> · ${esc(statusLabels[data.outcomes?.[s.id]?.status] || '未确认')}<br>${esc(data.outcomes?.[s.id]?.decision || '')}</p>`).join('')+'</details>':''}</div>`;
    const edit=editing[step.id] ?? false;
    const hasDraft=!!(draft.decision||draft.reason||draft.basis||draft.limits);
    $('flow-summarize').classList.toggle('primary',!hasDraft);
    if(hasDraft)$('flow-summarize').textContent='重新整理草稿';
    const fields=$('flow-edit-fields');fields.hidden=!edit;
    const note=document.createElement('section');note.className='outcome-note';note.hidden=edit||!hasDraft;
    note.innerHTML=[['本步决定','decision'],['采用理由','reason'],['依据','basis'],['限制与待办','limits']].map(([label,key])=>'<section><h4>'+label+'</h4><p>'+esc(draft[key]||'尚未填写')+'</p></section>').join('')+'<p class="flow-note">研究判断：'+esc(verdicts[draft.verdict]||verdicts.pending)+'</p><div class="evidence-footnotes">'+(draft.evidence_ids||[]).map(id=>{const i=records.findIndex(e=>e.id===id);return i<0?'':'<button class="sv-btn" data-cite="'+esc(id)+'">依据 '+String(i+1).padStart(2,'0')+'</button>';}).join('')+'</div>';
    fields.before(note);const editButton=document.createElement('button');editButton.className='sv-btn';editButton.id='flow-edit';editButton.textContent=edit?'返回草稿':hasDraft?'修改草稿':'手动填写';fields.before(editButton);
    wrap.querySelector('.flow-actions').hidden=!hasDraft&&!edit;
    editButton.onclick=()=>{if(!fields.hidden)read();editing[step.id]=fields.hidden;renderOutcome();if(editing[step.id])$('flow-edit-fields').querySelector('textarea').focus();else $('flow-edit').focus();};
    note.querySelectorAll('[data-cite]').forEach(b=>b.onclick=()=>flow.openEvidence(b.dataset.cite));
    const history=wrap.querySelector('details.flow-record');if(history&&history.querySelector('summary')?.textContent.includes('历史版本')){const trigger=document.createElement('button');trigger.className='sv-btn';trigger.textContent='历史版本';history.replaceWith(trigger);trigger.onclick=()=>{const dialog=document.createElement('dialog');dialog.className='flow-history-dialog';dialog.innerHTML='<button class="ui-btn" autofocus>关闭</button><h3>已确认的历史版本</h3>';const contents=document.createElement('div');contents.innerHTML=(o.history||[]).map(h=>'<article class="flow-record"><h4>第 '+h.version+' 版</h4>'+['decision','reason','basis','limits'].map(k=>'<p>'+esc(h[k]||'')+'</p>').join('')+'</article>').join('');dialog.append(contents);dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('click',e=>{if(e.target===dialog)dialog.close();});dialog.onclose=()=>{dialog.remove();trigger.focus();};document.body.append(dialog);dialog.showModal();};}
    const read = () => {
      const d={};wrap.querySelectorAll('[data-field]').forEach(el=>d[el.dataset.field]=el.type==='checkbox'?el.checked:el.value);
      d.evidence_ids=[...wrap.querySelectorAll('[data-evidence]:checked')].map(el=>el.dataset.evidence);drafts[step.id]=d;return d;
    };
    wrap.querySelectorAll('textarea,select,input').forEach(el=>el.addEventListener('input',read));
    const statusLine = (text, cls='flow-note') => { const node=$('flow-outcome-status'); if(node){node.className=cls;node.textContent=text;} };
    const action = async kind => {
      // busy 期间也给出明确反馈，避免按钮看起来「点了没反应」。
      if(busy){ statusLine('上一步操作还在进行，请稍候再点一次。'); return; }
      busy = true;
      try {
        const draft=read();
        statusLine(kind==='confirm'?'正在确认本步成果…':'正在保存草稿…');
        const saved=await request(`/api/solve/sessions/${encodeURIComponent(id)}/steps/${encodeURIComponent(step.id)}/outcome`,{...draft,expected_revision:draftRevisions[step.id],action:kind});
        flow.accept(saved);delete drafts[step.id];editing[step.id]=false;renderOutcome();
        statusLine(kind==='confirm'?'✓ 已确认，下一步由你主动进入。':'草稿已保存');
        if(kind==='confirm')window.dispatchEvent(new CustomEvent('studio-stage',{detail:flow.currentMaterial()}));
      } catch(e) {
        statusLine(e.message||'保存失败，请重试。','flow-error');
        const reload=$('flow-reload'); if(reload)reload.hidden=false;
      } finally {
        busy=false;
        if($('flow-next'))$('flow-next').disabled=data.outcomes?.[step.id]?.status!=='confirmed';
        flow.paint();
      }
    };
    $('flow-save').onclick=()=>action('save');$('flow-confirm').onclick=()=>action('confirm');
    $('flow-reload').onclick=async()=>{
      try{await flow.refresh();draftRevisions[step.id]=data.revision || 0;renderOutcome();$('flow-outcome-status').textContent='已读取最新版本，你的草稿仍保留。请核对下方最新内容后再提交。';const latest=document.createElement('section');latest.className='flow-record';latest.innerHTML='<h4>服务端最新成果</h4>'+['decision','reason','basis','limits'].map(k=>'<p>'+esc(data.outcomes?.[step.id]?.[k]||'尚未填写')+'</p>').join('');$('flow-outcome-status').after(latest);}
      catch(e){$('flow-outcome-status').textContent=e.message;}
    };
    if ($('flow-next')) $('flow-next').onclick=()=>api.enter(index+1);
    // 整理草稿改到专属论文区域执行；这里跳转过去，由那边完成整理、确认与后续衔接。
    $('flow-summarize').onclick=()=>{ if(busy||api.isBusy())return; openDraftPage(); };
  }
  function renderEvidence() {
    const {step,id}=current(); const wrap=$('sv-evidence');
    if(!step){wrap.innerHTML='<p>先选择一个建模步骤。</p>';return;}
    const records=(data.evidence || []).filter(e=>e.step_id===step.id), draft=evidenceDrafts[step.id] || {};
    const scroll=wrap.scrollTop,openRecords=[...wrap.querySelectorAll('details[open]')].map(d=>d.dataset.keep).filter(Boolean);
    wrap.innerHTML=`<div><h3>实验记录</h3><p class="flow-note">把运行输出、理论推导或外部结果放在这里，再解释它能支持什么。</p><p class="flow-note">研究判断：${esc(verdicts[data.outcomes?.[step.id]?.verdict]||verdicts.pending)} · <button class="sv-link-btn" id="verify-outcome-link">${data.outcomes?.[step.id]?.decision?'查看关联成果':'补充结论与限制'}</button></p>
      ${[...records].reverse().map((e,i)=>`${i===1?'<details class="verification-history" data-keep="history"><summary>历史记录</summary>':''}<article class="flow-record" data-record="${esc(e.id)}"><b>${esc(e.purpose)}</b><p class="flow-note">${esc(sources[e.source] || e.source)} · ${new Date(e.created_at*1000).toLocaleString('zh-CN')} · ${e.status==='success'?'运行完成（仍需判断模型是否成立）':e.status==='failed'?'运行失败，请查看详情':'材料已提供'} · 成果版本 ${e.outcome_version||0}</p>${e.status==='failed'?'<p class="flow-error">'+esc((e.stderr||e.stdout||'执行未完成，请查看失败详情。').trim().split(/\r?\n/).filter(Boolean).at(-1).slice(0,240))+'</p>':'<pre>'+esc(e.stdout||e.stderr||'没有文字输出')+'</pre>'}${(e.images || []).map(img=>`<img src="/api/solve/sessions/${encodeURIComponent(id)}/assets/${encodeURIComponent(img.asset)}" alt="${esc(img.name)}">`).join('')}<details data-keep="${esc(e.id)}"><summary>${e.status==='failed'?'查看失败详情与归档代码':'归档代码、原始输出与输入材料'}</summary><pre>${esc(e.code || '')}</pre><pre>${esc(e.stdout || '')}</pre><pre>${esc(e.stderr || '')}</pre>${(e.inputs || []).map(a=>a.asset?`<a href="/api/solve/sessions/${encodeURIComponent(id)}/assets/${encodeURIComponent(a.asset)}" download="${esc(a.name)}">${esc(a.name)}</a>`:`<p>${esc(a.name)}：${esc(a.unavailable)}</p>`).join('<br>')}</details><button class="sv-btn" data-discuss="${esc(e.id)}">请助教解释这份结果</button></article>${i===records.length-1&&i>0?'</details>':''}`).join('') || '<p class="flow-note">还没有本步验证记录。运行原有编辑器中的代码后，结果会自动保存在这里。</p>'}
      <details class="flow-record" ${draft.stdout?'open':''}><summary>记录推导或补充外部结果</summary><label>验证目的<input id="flow-e-purpose" value="${esc(draft.purpose)}"></label><label>来源<select id="flow-e-source">${['theory','user','simulation'].map(k=>`<option value="${k}" ${draft.source===k?'selected':''}>${sources[k]}</option>`).join('')}</select></label><label>结果、推导与出处<textarea id="flow-e-text" rows="5">${esc(draft.stdout)}</textarea></label><button id="flow-e-save" class="sv-btn">保存这份依据</button><p id="flow-e-status" role="status" class="flow-note"></p></details></div>`;
    $('verify-outcome-link').onclick=()=>flow.view('outcome');
    wrap.scrollTop=scroll;openRecords.forEach(key=>wrap.querySelector('[data-keep="'+CSS.escape(key)+'"]')?.setAttribute('open',''));
    wrap.querySelectorAll('img').forEach(img=>{const button=document.createElement('button');button.className='evidence-image';button.setAttribute('aria-label','放大 '+img.alt);img.replaceWith(button);button.append(img);button.onclick=()=>ResearchUI.image(img.src,img.alt);});
    const read=()=>evidenceDrafts[step.id]={purpose:$('flow-e-purpose').value,source:$('flow-e-source').value,stdout:$('flow-e-text').value};
    ['flow-e-purpose','flow-e-source','flow-e-text'].forEach(i=>$(i).oninput=read);
    $('flow-e-save').onclick=async()=>{
      const draft=read();$('flow-e-save').disabled=true;
      try{await request(`/api/solve/sessions/${encodeURIComponent(id)}/steps/${encodeURIComponent(step.id)}/evidence`,draft);delete evidenceDrafts[step.id];await flow.refresh();renderEvidence();}
      catch(e){$('flow-e-status').textContent=e.message;}
      finally{$('flow-e-save').disabled=false;}
    };
    wrap.querySelectorAll('[data-discuss]').forEach(b=>b.onclick=()=>{const e=records.find(x=>x.id===b.dataset.discuss);flow.ask(`请解释验证记录 ${e.id}：${e.purpose}。它是否支持我的想法？有哪些限制？\n\n${e.stdout || e.stderr || ''}`);});
    window.ResearchMaterials?.evidence(wrap,records,{sessionId:id,stepId:step.id});
  }
  window.SolveFlow=flow;
})();
