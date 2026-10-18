/* Solve-only task workspace. Reuses the existing CodeMirror and chat instances. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => MMRender.escapeHtml(String(value ?? ''));
  const enc = encodeURIComponent;
  const labels = {pending:'排队中',running:'进行中',paused:'已暂停',interrupted:'已中断',failed:'失败',completed:'已完成',cancelled:'已取消',stale:'需复核',waiting_input:'需要补充材料'};
  const phases = {analysis:'提取思路',verification:'计算验证',writing:'整理草稿',paper:'生成论文',completed:'完成'};
  let adapter, context = {}, snapshot = {jobs:[],artifacts:[]}, stream, retryTimer, cursor = 0, refreshing;
  let selectedPanel = 'code', currentCode = null, editorBaseline = '', applyingCode = false, pendingCode = null;
  let sessionEpoch = 0, attached = false, refreshAgain = false;
  let studioSnapshotSeen=false, studioSnapshotKey='';
  const openedJobs = new Set(), dismissedJobs = new Set(), materialDrafts = new Map();
  const base = () => '/api/solve/sessions/' + enc(context.sessionId);
  const codeText = () => window.IDE?.editor.getValue() || '';
  const codeDirty = () => codeText() !== editorBaseline;
  const currentArtifacts = kind => (snapshot.artifacts || []).filter(a => a.kind === kind && (!a.step_id || a.step_id === context.stepId));

  async function request(url, method = 'GET', body) {
    const response = await fetch(url, {method, ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(typeof result.detail === 'string' ? result.detail : response.status === 409 ? '内容已有新版本，你的编辑已保留。请查看最新版本后合并。' : '操作失败，请重试');
      error.status = response.status; throw error;
    }
    return result;
  }
  function message(text) { if ($('wf-notice')) $('wf-notice').textContent = text; }
  function button(text, action, className = 'sv-btn') {
    const node = document.createElement('button'); node.type='button';node.className=className;node.textContent=text;node.onclick=action;return node;
  }
  function mount() {
    document.body.classList.add('workflow-workspace','studio-solve');
    // The shared IDE starts with an input() demo. Solve restores its own saved
    // editor later; a new project must start empty so generated code can open.
    applyingCode=true;window.IDE?.editor.setValue('');applyingCode=false;
    const top = document.querySelector('.sv-top');
    const step = document.createElement('span');step.id='wf-current-step';step.className='wf-current-step';top.append(step);
    const mode = document.createElement('label');mode.className='wf-mode';mode.innerHTML='<span class="wf-sr-only">对话模式</span><select id="wf-mode" aria-label="对话模式"><option value="tutor">辅导模式</option><option value="assistant">助手模式</option></select>';top.append(mode);
    const status = button('任务',()=>open(selectedPanel));status.id='wf-status';status.setAttribute('aria-live','polite');top.append(status);
    const tools = document.createElement('details');tools.id='wf-tools';tools.className='wf-tools';
    tools.innerHTML='<summary>工具</summary><div class="wf-tools-drawer"><div class="wf-drawer-title">工作区工具<button type="button" id="wf-tools-close" class="sv-btn" aria-label="关闭工具">×</button></div><details id="wf-environment"><summary>文件与工作环境</summary><div id="wf-environment-body"></div></details><details id="wf-settings"><summary>模型与连接设置</summary><div id="wf-settings-body"></div></details><details id="wf-terminal"><summary>终端与完整日志</summary><div id="wf-terminal-body"></div></details><details id="wf-history"><summary>材料历史与回退</summary><div id="wf-history-body"></div></details><details id="wf-task-history"><summary>任务记录</summary><div id="wf-jobs"></div></details><div id="wf-other-tools"></div></div>';
    top.append(tools);$('wf-tools-close').onclick=()=>{tools.open=false;tools.querySelector('summary').focus();};
    ['workdir','filename','sv-work-environment','ep-export'].forEach(id=>{if($(id))$('wf-environment-body').append($(id));});
    ['model-selector','btn-settings','ui-model-settings'].forEach(id=>{if($(id))$('wf-settings-body').append($(id));});
    ['sv-reset','sv-save-state'].forEach(id=>{if($(id))$('wf-other-tools').append($(id));});
    const companion=button('研究手记',()=>{tools.open=false;window.ResearchCompanion?.openNotebook();});companion.id='wf-companion-toggle';$('wf-other-tools').append(companion);
    const companionSettings=document.querySelector('.companion-settings');if(companionSettings)$('wf-settings-body').append(companionSettings);
    // 终端现在住在中栏的「结果」tab 里，不再往抽屉里搬第二份；这里只留一个跳转。
    const terminalLink=button('去「结果」tab 看终端与日志',()=>{tools.open=false;open('results');});
    terminalLink.id='wf-terminal-link';$('wf-terminal-body').append(terminalLink);
    document.querySelector('.chat-model-tools')?.remove();
    $('wf-tools').addEventListener('toggle',()=>{if(tools.open)renderHistory();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){document.body.classList.remove('wf-companion-visible');if(tools.open){tools.open=false;tools.querySelector('summary').focus();}}});
    document.addEventListener('click',e=>{if(tools.open&&!tools.contains(e.target)&&!e.target.closest('dialog,.settings-overlay,.model-menu'))tools.open=false;});

    const mid = document.querySelector('.sv-col-mid');mid.id='wf-task-panel';mid.setAttribute('aria-label','当前任务');
    const head=document.createElement('div');head.className='wf-task-head';head.innerHTML='<nav class="wf-task-tabs" aria-label="任务内容"><button data-task="code">代码</button><button data-task="results">结果</button><button data-task="note">笔记</button><button data-task="paper">论文草稿</button></nav><button id="wf-close" class="sv-btn" title="收起任务面板" aria-label="收起任务面板">×</button>';
    mid.prepend(head);head.querySelectorAll('[data-task]').forEach(node=>node.onclick=()=>open(node.dataset.task));$('wf-close').onclick=close;
    const notice=document.createElement('p');notice.id='wf-notice';notice.className='wf-notice';notice.setAttribute('role','status');head.after(notice);
    const jobbar=document.createElement('div');jobbar.id='wf-current-job';jobbar.className='wf-current-job';notice.after(jobbar);
    const codeNotice=document.createElement('div');codeNotice.id='wf-code-notice';codeNotice.className='wf-code-notice';codeNotice.hidden=true;jobbar.after(codeNotice);
    const saveCode=button('保存代码版本',()=>saveCodeVersion().catch(e=>message(e.message)));saveCode.id='wf-save-code';$('sv-editor-tools').append(saveCode);
    $('btn-save').textContent='保存到文件';
    const ask=button('选中代码问 AI',()=>{const ed=window.IDE?.editor;adapter.append('请帮我检查这段代码：\n```python\n'+(ed?.getSelection()||codeText())+'\n```');});$('sv-editor-tools').append(ask);
    const content=mid.querySelector('.verify-content');
    for(const kind of ['note','paper']){const section=document.createElement('section');section.id='wf-'+kind;section.className='wf-materials';section.hidden=true;section.setAttribute('aria-label',kind==='note'?'学习笔记':'论文待审草稿');content.append(section);}
    content.append($('sv-outcome'));
    // 底部只保留「从讨论中整理草稿」这一个入口；原先重复的「确认本步研究判断」
    // 只是再次切到本步成果页，和红色主按钮功能重合，已移除。
    const footer=document.createElement('div');footer.className='wf-task-footer';mid.append(footer);
    const help=button('给我一个具体方案',()=>{help.hidden=true;adapter.send('我现在卡住了，请切换到助手方式，给我一个具体可操作的方案和备选思路，并说明依据。');});help.id='wf-offer-help';help.hidden=true;document.querySelector('.sv-composer').before(help);
    const effective=document.createElement('span');effective.id='wf-effective-mode';effective.className='wf-effective-mode';document.querySelector('.sv-composer-hint').append(effective);
    $('wf-mode').onchange=async()=>{const wanted=$('wf-mode').value;$('wf-mode').disabled=true;try{await request(base()+'/workflow/mode','POST',{mode:wanted});snapshot.mode=wanted;$('wf-mode').title=wanted==='assistant'?'主动给出方案与判断':'围绕关键问题引导，需要时可直接求助';message('');}catch(e){$('wf-mode').value=snapshot.mode||'tutor';message(e.message);}finally{$('wf-mode').disabled=false;}};
    $('btn-run').addEventListener('click',e=>{if(!context.sessionId)return;e.preventDefault();e.stopImmediatePropagation();runCode();},true);
    $('btn-stop').addEventListener('click',e=>{const job=activeJob();if(!job)return;e.preventDefault();e.stopImmediatePropagation();control(job,'cancel');},true);
    window.IDE?.editor.setOption('extraKeys',{'Ctrl-Enter':runCode,'Cmd-Enter':runCode});
    window.IDE?.editor.on('change',()=>{if(!applyingCode){$('wf-save-code').textContent=codeDirty()?'保存代码版本 •':'保存代码版本';stashCode();}});
    window.addEventListener('solve-evidence',()=>refresh().catch(()=>{}));
    $('sv-tabs').querySelector('[data-view="verification"]').textContent='任务面板';
    mountStudio(top, mode, tools);
    document.body.dataset.workflowPanel='code';
    editorBaseline=codeText();
  }
  function dockStudioHeader(header){
    const top=document.querySelector('.sv-top');
    if(!header || !top || !$('wf-settings-body'))return;
    // ui.js can mount after the project has loaded. Reconcile ownership only;
    // never rebuild the editor, workspace panels, stage controls, or listeners.
    ['model-selector','btn-settings','ui-model-settings'].forEach(id=>{const node=$(id);if(node && node.parentNode!==$('wf-settings-body'))$('wf-settings-body').append(node);});
    const environment=$('sv-work-environment');
    if(environment && environment.parentNode!==$('wf-environment-body'))$('wf-environment-body').append(environment);
    document.querySelectorAll('.companion-settings').forEach(node=>{if(node.parentNode!==$('wf-settings-body'))$('wf-settings-body').append(node);});
    document.querySelectorAll('.chat-model-tools').forEach(node=>node.remove());
    const globalMenu=header.querySelector(':scope > .ui-menu');
    if(globalMenu){globalMenu.classList.add('studio-global-menu');globalMenu.querySelector('summary').textContent='更多页面与使用引导';$('wf-other-tools').append(globalMenu);}
    if(top.parentNode!==header)header.insertBefore(top,header.querySelector(':scope > .ui-header-end'));
    const back=header.querySelector('.ui-back');if(back){back.textContent='← 项目';back.title='返回我的项目';}
    const brand=header.querySelector('.ui-brand');if(brand)brand.setAttribute('aria-label','建模研究台');
  }
  function mountStudio(top,mode,tools){
    // Both late ui.js and an already mounted header use the same idempotent hook.
    document.addEventListener('research-ui-ready',event=>dockStudioHeader(event.detail?.header));
    if(window.ResearchUI?.ready)window.ResearchUI.ready.then(dockStudioHeader);
    dockStudioHeader(document.querySelector('.ui-header[data-research-ui-ready="true"]'));
    const personality=document.createElement('div');personality.id='studio-personality-slot';top.insertBefore(personality,tools);
    const tabs=$('sv-tabs');tabs.setAttribute('aria-label','研究阶段与工作材料');
    $('sv-route-toggle').textContent='研究路线';
    const stageInfo=document.createElement('div');stageInfo.className='studio-stage-info';stageInfo.innerHTML='<span class="studio-stage-number" id="studio-stage-number">01</span><div><strong id="studio-stage-title">把问题想清楚</strong><span id="studio-stage-action">先从你的直觉开始。</span></div>';
    $('sv-route-toggle').after(stageInfo);
    const discussion=tabs.querySelector('[data-view="discussion"]');discussion.textContent='只看讨论';
    tabs.querySelector('[data-view="verification"]').textContent='代码与记录';tabs.querySelector('[data-view="outcome"]').textContent='本步成果';
    $('sv-roadmap').before($('sv-confirm-progress'));
    const stage=document.querySelector('.sv-stage'), contextBar=document.createElement('div');contextBar.className='studio-context-bar';
    const brief=document.createElement('details');brief.id='studio-stage-brief';brief.innerHTML='<summary>本步要做什么</summary>';brief.append($('sv-step-head'));
    const source=document.createElement('details');source.id='studio-project-source';source.innerHTML='<summary>题目与我的想法</summary><div class="studio-source-body"></div>';
    source.querySelector('div').append($('sv-bg-toggle'),$('sv-idea'));
    contextBar.append(brief,source);stage.prepend(contextBar);
    const helper=document.querySelector('.sv-composer-hint');helper.prepend(mode);
    const tip=document.createElement('span');tip.className='studio-input-tip';tip.textContent='卡住了，就说“给我一个思路”。';helper.append(tip);
    const summarize=$('sv-draft-from-chat');if(summarize)document.querySelector('.wf-task-footer').prepend(summarize);
    $('sv-input').placeholder='把你的想法放在这里，我们一起往下想…';
    $('wf-status').title='查看任务进度与研究记录';
    window.dispatchEvent(new CustomEvent('studio-ready'));
  }
  function attach(next) {
    if(!attached)return;
    const changed=context.sessionId!==next.sessionId;
    context={...context,...next};$('wf-current-step').textContent=next.stepTitle || context.stepId || '';
    document.body.classList.toggle('workflow-ready',!!context.sessionId);$('wf-mode').disabled=!context.sessionId;
    if(changed){sessionEpoch++;stream?.close();clearTimeout(retryTimer);cursor=0;snapshot={jobs:[],artifacts:[]};currentCode=null;pendingCode=null;openedJobs.clear();dismissedJobs.clear();editorBaseline=codeText();refreshing=null;studioSnapshotSeen=false;studioSnapshotKey='';loadDrafts();}
    if(context.sessionId)refresh().then(()=>{if(changed)connect();}).catch(e=>{message(e.message);if(changed)connect();});
  }
  function draftKey(){return 'modeling.workflow.drafts.'+context.sessionId;}
  function loadDrafts(){materialDrafts.clear();try{const saved=JSON.parse(localStorage.getItem(draftKey())||'{}');Object.entries(saved.materials||{}).forEach(([id,d])=>materialDrafts.set(id,d));(saved.dismissedJobs||[]).forEach(id=>dismissedJobs.add(id));if(saved.code){applyingCode=true;window.IDE?.editor.setValue(saved.code.content);applyingCode=false;editorBaseline=saved.code.baseline;currentCode=saved.code.ref;}}catch{}}
  function stashCode(){if(!context.sessionId)return;try{localStorage.setItem(draftKey(),JSON.stringify({materials:Object.fromEntries(materialDrafts),dismissedJobs:[...dismissedJobs],code:codeDirty()?{content:codeText(),baseline:editorBaseline,ref:currentCode}:null}));}catch{message('浏览器草稿空间不足，请保存到项目。');}}
  function connect(){
    stream?.close();if(!context.sessionId)return;const epoch=sessionEpoch;
    stream=new EventSource(base()+'/workflow/events?after='+cursor);
    stream.addEventListener('workflow',event=>{if(epoch!==sessionEpoch)return;try{const data=JSON.parse(event.data);cursor=Math.max(cursor,Number(data.id||data.event_id||0));refresh().catch(()=>{});if(data.evidence_id||data.type?.includes('evidence')||data.status==='completed')SolveFlow.refresh().catch(()=>{});}catch{}});
    stream.onopen=()=>clearTimeout(retryTimer);
    stream.onerror=()=>{stream?.close();clearTimeout(retryTimer);retryTimer=setTimeout(async()=>{if(epoch!==sessionEpoch)return;await refresh().catch(()=>{});connect();},3000);};
  }
  async function refresh(){
    if(!context.sessionId)return;if(refreshing){refreshAgain=true;return refreshing;}
    const epoch=sessionEpoch;
    refreshing=request(base()+'/workflow').then(value=>{if(epoch!==sessionEpoch)return;snapshot=value;cursor=Math.max(cursor,Number(value.last_event_id||0));render();return value;}).finally(()=>{if(epoch===sessionEpoch){refreshing=null;if(refreshAgain){refreshAgain=false;queueMicrotask(()=>refresh().catch(()=>{}));}}});return refreshing;
  }
  function activeJob(){return (snapshot.jobs||[]).slice().reverse().find(j=>['pending','running'].includes(j.status));}
  function render(){
    $('wf-mode').value=snapshot.mode||'tutor';const jobs=snapshot.jobs||[],job=activeJob();
    if(snapshot.offer_concrete_help)$('wf-offer-help').hidden=false;
    const latest=job||jobs.at(-1);$('wf-status').textContent=job?(phases[job.phase]||'任务')+' · '+(labels[job.status]||job.status):latest?'任务 · '+(labels[latest.status]||latest.status):'任务';
    $('btn-run').disabled=!!job;$('btn-stop').disabled=!job;
    renderJob($('wf-current-job'),latest);renderJobHistory();
    for(const item of jobs){
      if(item.phase==='verification'&&['pending','running'].includes(item.status)&&!openedJobs.has(item.id)&&!dismissedJobs.has(item.id)){
        openedJobs.add(item.id);open('results',false);
      }
    }
    const code=currentArtifacts('code').at(-1);
    if(code&&(!currentCode||code.id!==currentCode.artifact_id||code.revision!==currentCode.revision)){
      if(code.content===codeText()){currentCode={artifact_id:code.id,revision:code.revision};editorBaseline=codeText();stashCode();}
      else if(!codeDirty()&&(!codeText().trim()||currentCode))loadCode(code);
      else offerCode(code);
    }
    renderMaterials('note');renderMaterials('paper');renderHistory();
    const acceptance=snapshot.step_acceptance||null;
    const stateKey=JSON.stringify([context.sessionId,context.stepId,jobs.map(j=>[j.id,j.status,j.phase]),acceptance?.step_id,acceptance?.updated_at]);
    if(stateKey!==studioSnapshotKey){
      window.dispatchEvent(new CustomEvent('studio-workflow',{detail:{sessionId:context.sessionId,stepId:context.stepId,jobs:structuredClone(jobs),acceptance:acceptance?structuredClone(acceptance):null,initial:!studioSnapshotSeen}}));
      studioSnapshotSeen=true;studioSnapshotKey=stateKey;
    }
  }
  function renderJob(target,job){
    target.replaceChildren();if(!job)return;
    const label=document.createElement('span');label.textContent=(phases[job.phase]||'任务')+' · '+(labels[job.status]||job.status)+(job.purpose?' · '+job.purpose:'');target.append(label);
    const actions=job.status==='running'||job.status==='pending'?['pause','cancel']:job.status==='paused'?['resume','cancel']:['failed','interrupted','waiting_input','stale'].includes(job.status)?['retry','cancel']:job.status==='completed'&&job.warnings?.length?['retry']:[];
    const names={pause:'暂停',resume:'继续',retry:'重试',cancel:'取消'};actions.forEach(action=>target.append(button(names[action],()=>control(job,action))));
    if(job.warnings?.length){const warnings=document.createElement('details');warnings.className='wf-job-warnings';const summary=document.createElement('summary');summary.textContent='待完善事项';const text=document.createElement('p');text.textContent=job.warnings.join('；');warnings.append(summary,text);target.append(warnings);}
    const paperId=job.result?.paper_session_id;if(paperId){const link=document.createElement('a');link.className='sv-btn';link.href='/paper?session_id='+enc(paperId);link.textContent='打开正式论文';target.append(link);}
    if(job.error||job.missing_inputs?.length){const error=document.createElement('p');error.className='wf-job-error';error.textContent=job.error || job.missing_inputs.join('；');target.append(error);}
  }
  function renderJobHistory(){const target=$('wf-jobs');target.replaceChildren();for(const job of (snapshot.jobs||[]).slice().reverse()){const row=document.createElement('div');row.className='wf-history-row';renderJob(row,job);target.append(row);}if(!target.children.length)target.textContent='讨论产生任务后会保存在这里。';}
  async function control(job,action){try{await request(base()+'/workflow/'+enc(job.id)+'/control','POST',{action,operation_id:crypto.randomUUID()});await refresh();}catch(e){message(e.message);}}
  function setPanel(kind){
    selectedPanel=kind;document.body.dataset.workflowPanel=kind;document.body.classList.add('workflow-panel-open');
    document.querySelectorAll('[data-task]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.task===kind)));
    $('sv-outcome').hidden=kind!=='outcome';$('sv-evidence').hidden=kind!=='results';$('wf-note').hidden=kind!=='note';$('wf-paper').hidden=kind!=='paper';
    // 编辑器/实验记录/终端/「这次计算想验证什么」的显隐由任务 tab 统一决定。
    SolveFlow.applyPanel?.(kind);
    window.IDE?.editor.refresh();
  }
  function open(kind='code',user=true){
    if(!context.sessionId)return;
    const focus=document.activeElement;
    if(kind==='outcome')SolveFlow.view('outcome');else SolveFlow.view('verification');
    setPanel(kind);if(!user&&focus?.isConnected)focus.focus({preventScroll:true});
  }
  function close(){for(const job of snapshot.jobs||[])if(['running','pending'].includes(job.status))dismissedJobs.add(job.id);stashCode();document.body.classList.remove('workflow-panel-open');SolveFlow.view('discussion');$('wf-status').focus({preventScroll:true});}
  function onFlowView(name){if(!attached)return;if(name==='verification')setPanel(selectedPanel==='outcome'?'code':selectedPanel);else if(name==='outcome')setPanel('outcome');else document.body.classList.remove('workflow-panel-open');}
  function loadCode(artifact){
    applyingCode=true;window.EditorPreview?.setTab('code');window.IDE?.editor.setValue(artifact.content||'');window.IDE?.editor.setOption('mode','python');applyingCode=false;
    editorBaseline=codeText();currentCode=artifact.id?{artifact_id:artifact.id,revision:artifact.revision}:null;pendingCode=null;$('wf-code-notice').hidden=true;$('wf-save-code').textContent='保存代码版本';stashCode();
  }
  function offerCode(artifact){pendingCode=artifact;const box=$('wf-code-notice');box.hidden=false;box.replaceChildren();const label=document.createElement('span');label.textContent='有新的代码建议，你的编辑已保留。';box.append(label,button('查看并载入',()=>previewCode(artifact)));}
  function previewCode(artifact){
    const dialog=document.createElement('dialog');dialog.className='flow-history-dialog';dialog.innerHTML='<button type="button" class="sv-btn">关闭</button><h3>代码建议</h3><p>载入前会在当前浏览器保留现有编辑副本。</p><pre></pre>';
    dialog.querySelector('pre').textContent=artifact.content||'';dialog.querySelector('button').onclick=()=>dialog.close();dialog.append(button('载入这版代码',()=>{if(codeText().trim()){materialDrafts.set('__editor_backup__',{content:codeText(),revision:currentCode?.revision||0,title:'载入建议前的代码'});stashCode();}loadCode(artifact);dialog.close();open('code');}));dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  async function saveCodeVersion(){
    if(!currentCode){message('这段代码将在运行验证时保存为独立版本，也可使用“保存到文件”。');return;}
    const text=codeText(),ref={...currentCode};
    try{const result=await request(base()+'/artifacts/'+enc(ref.artifact_id),'PUT',{expected_revision:ref.revision,content:text});const saved=result.artifact||result;if(currentCode?.artifact_id===ref.artifact_id){currentCode={artifact_id:ref.artifact_id,revision:saved.revision||ref.revision+1};editorBaseline=text;stashCode();}message('代码版本已保存。');await refresh();}catch(e){message(e.message);if(e.status===409)await refresh();throw e;}
  }
  async function runCode(){
    if(!context.sessionId||activeJob())return;
    const code=codeText();if(!code.trim()){message('先写入代码再运行。');return;}
    if(currentCode&&codeDirty()){try{await saveCodeVersion();}catch{return;}}
    try{await adapter.save();await request(base()+'/workflow/code','POST',{step_id:context.stepId,code,purpose:$('sv-purpose').value.trim()||context.stepTitle||'验证当前方案',operation_id:crypto.randomUUID()});message('已开始验证，输出在「结果」tab；期间可以继续讨论。');open('results',false);await refresh();}catch(e){message(e.message);}
  }
  function noteProvenance(artifact){
    const note=artifact.metadata?.note;const info=document.createElement('div');info.className='wf-provenance';
    const origins={student:'学生提出',student_proposed:'学生提出',ai:'AI 建议',ai_suggested:'AI 建议',student_adopted:'学生采纳',adopted:'学生采纳',unknown:'来源待核对'};
    const origin=note?.attribution||note?.origin||note?.source||artifact.author;info.textContent=(origins[origin]||'待核对来源')+' · '+(artifact.source_message_ids?.length||0)+' 条来源消息 · '+(artifact.evidence_ids?.length||0)+' 项验证记录';
    return info;
  }
  function renderMaterials(kind){
    const target=$('wf-'+kind),artifacts=currentArtifacts(kind).filter(a=>a.status!=='adopted'),known=new Set(artifacts.map(a=>a.id));
    window.ResearchMaterials?.heading(target,kind,context,refresh);
    target.querySelectorAll('.wf-artifact').forEach(node=>{if(!known.has(node.dataset.id)&&!materialDrafts.has(node.dataset.id))node.remove();else node.hidden=!known.has(node.dataset.id);});
    target.querySelector('.wf-empty')?.remove();
    if(!artifacts.length){const p=document.createElement('p');p.className='wf-empty';p.textContent=kind==='note'?'讨论后自动整理学生思路、AI 建议与待解决问题，并保留来源。':'讨论与验证完成后，这里会出现对应章节的待审草稿。';target.append(p);return;}
    for(const artifact of artifacts){
      let node=[...target.querySelectorAll('.wf-artifact')].find(n=>n.dataset.id===artifact.id);
      if(!node){node=document.createElement('article');node.className='wf-artifact';node.dataset.id=artifact.id;target.append(node);}
      if(node.dataset.revision===String(artifact.revision)&&node.dataset.status===artifact.status&&node.children.length)continue;
      if(materialDrafts.has(artifact.id)&&node.querySelector('textarea')){node.querySelector('.wf-material-state').textContent='有新版本。你的编辑保留在此，保存时会检查版本。';continue;}
      renderArtifact(node,artifact);
    }
  }
  function renderArtifact(node,artifact){
    node.replaceChildren();node.dataset.revision=String(artifact.revision);node.dataset.status=artifact.status;
    const title=document.createElement('h3');title.textContent=artifact.title||(artifact.kind==='note'?'学习笔记':'论文待审草稿');node.append(title);
    const state=document.createElement('p');state.className='wf-material-state';state.textContent='第 '+artifact.revision+' 版 · '+(artifact.status==='needs_review'?'需要重新复核':artifact.suggested_for?'修改建议，尚未写入原稿':artifact.human_edited?'你已编辑 · 保留原稿':'待确认');node.append(state);
    if(artifact.kind==='note')node.append(noteProvenance(artifact));
    const preview=document.createElement('div');preview.className='panel-md wf-material-preview';MMRender.renderMarkdown(preview,artifact.content||'');node.append(preview);
    const edit=document.createElement('textarea');edit.setAttribute('aria-label','编辑'+title.textContent);edit.value=materialDrafts.get(artifact.id)?.content??artifact.content??'';edit.hidden=!materialDrafts.has(artifact.id);node.append(edit);preview.hidden=!edit.hidden;
    const buttons=document.createElement('div');buttons.className='wf-material-actions';node.append(buttons);
    buttons.append(button('修改',()=>{edit.hidden=false;preview.hidden=true;materialDrafts.set(artifact.id,{content:edit.value,revision:artifact.revision,title:artifact.title});stashCode();edit.focus();}));
    const save=button('保存修改',async()=>{const draft=materialDrafts.get(artifact.id)||{revision:artifact.revision},submitted=edit.value,epoch=sessionEpoch,url=base()+'/artifacts/'+enc(artifact.id);save.disabled=true;try{const saved=await request(url,'PUT',{expected_revision:draft.revision,content:submitted});if(epoch!==sessionEpoch)return;const current=materialDrafts.get(artifact.id);if(!current||current.content===submitted)materialDrafts.delete(artifact.id);else current.revision=saved.revision;stashCode();node.dataset.revision='';await refresh();}catch(e){if(epoch===sessionEpoch)state.textContent=e.message;}finally{save.disabled=false;}});buttons.append(save);
    buttons.append(button('查看最新版本',()=>{const current=snapshot.artifacts.find(a=>a.id===artifact.id)||artifact;showVersion(current,current.revision,current.content);}));
    if(artifact.kind==='paper')buttons.append(button('带入本步成果',()=>{adapter.append('请结合这份待审草稿，帮我检查本步研究判断：\n\n'+edit.value);message('草稿已放入对话输入框，由你确认后发送。');}));
    edit.oninput=()=>{const draft=materialDrafts.get(artifact.id)||{revision:artifact.revision,title:artifact.title};draft.content=edit.value;materialDrafts.set(artifact.id,draft);stashCode();state.textContent='修改已暂存在当前浏览器';};
    const originSession=context.sessionId;
    window.ResearchMaterials?.materialActions(node,artifact,{...context},{artifacts:()=>snapshot.artifacts,draft:id=>materialDrafts.get(id),adopted:async(id,original)=>{if(context.sessionId!==originSession)return;if(materialDrafts.get(id)?.content===original)materialDrafts.delete(id);stashCode();await refresh();message('已保存为新版本。研究判断需要你重新确认。');}});
  }
  function renderHistory(){
    const target=$('wf-history-body');if(!target)return;target.replaceChildren();
    for(const artifact of snapshot.artifacts||[]){const row=document.createElement('details');const summary=document.createElement('summary');summary.textContent=(artifact.title||artifact.kind)+' · 第 '+artifact.revision+' 版';row.append(summary);for(const version of artifact.versions||[])row.append(button('查看第 '+version.revision+' 版',()=>showVersion(artifact,version.revision,version.content)));row.append(button('查看当前版本',()=>showVersion(artifact,artifact.revision,artifact.content)));target.append(row);}
    const backup=materialDrafts.get('__editor_backup__');if(backup)target.append(button('恢复载入建议前的代码',()=>previewCode({content:backup.content})));
    if(!target.children.length)target.textContent='材料生成后，在这里查看旧版本或回退。';
  }
  function showVersion(artifact,revision,content){
    const dialog=document.createElement('dialog');dialog.className='flow-history-dialog';dialog.innerHTML='<button type="button" class="sv-btn">关闭</button><h3></h3><pre></pre><p role="status"></p>';
    dialog.querySelector('h3').textContent=(artifact.title||'材料')+' · 第 '+revision+' 版';dialog.querySelector('pre').textContent=content||'';dialog.querySelector('button').onclick=()=>dialog.close();
    if(revision!==artifact.revision)dialog.append(button('将此版恢复为新版本',async()=>{try{await request(base()+'/artifacts/'+enc(artifact.id)+'/restore','POST',{expected_revision:artifact.revision,revision});await refresh();dialog.close();message('已恢复为新版本，下游成果需要重新复核。');}catch(e){dialog.querySelector('[role=status]').textContent=e.message;}}));
    if(artifact.kind!=='code')dialog.append(button('以最新版为基础继续合并',()=>{const draft=materialDrafts.get(artifact.id);if(draft){draft.revision=artifact.revision;stashCode();}dialog.close();message('你的文字已保留，请对照最新版本修改后保存。');}));
    dialog.onclose=()=>dialog.remove();document.body.append(dialog);dialog.showModal();
  }
  window.ModelingWorkflow={
    init(api){if(attached)return;adapter=api;mount();attached=true;},attach,refresh,open,onFlowView,
    detach(){stream?.close();clearTimeout(retryTimer);sessionEpoch++;context={};snapshot={jobs:[],artifacts:[]};document.body.classList.remove('workflow-ready','workflow-panel-open');},
    stepAcceptance:()=>snapshot.step_acceptance||null,
    isDirty:()=>!!context.sessionId&&(codeDirty()||[...materialDrafts.keys()].some(id=>id!=='__editor_backup__')),
    isApplyingCode:()=>applyingCode,
    chatContext:()=>({...(currentCode?{code_ref:{...currentCode}}:{})}),
    editorContext:()=>codeText().trim()?{content:codeText(),artifact_id:currentCode?.artifact_id||null,revision:currentCode?.revision||null,dirty:codeDirty()}:null,
    chatMeta(event){if(event.mode)$('wf-effective-mode').textContent='本轮：'+(event.mode==='assistant'?'助手模式':'辅导模式');$('wf-offer-help').hidden=!event.offer_help;},
    proposeCode(content){open('code');if(codeText().trim())offerCode({content});else loadCode({content});},
    async proposeFile(path){try{const result=await request('/api/ide/read?path='+enc(path));if(result.ok){offerCode({content:result.content||''});message('AI 已生成文件，可在代码面板查看后载入。');}}catch(e){message(e.message);}},
  };
})();
