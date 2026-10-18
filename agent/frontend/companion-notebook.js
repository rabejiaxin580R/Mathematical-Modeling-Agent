/* Project-grounded companion notes. Research content stays in its original store. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const enc = encodeURIComponent;
  const pid = () => {try{return localStorage.getItem('mm_profile_id') || '';}catch{return '';}};
  const uuid = () => crypto.randomUUID();
  let snapshot = {}, state = {}, prefs = {}, context = null, scope = '', signature = '';
  let epoch = 0, controller, pending, refreshTimer, paintTimer, editing = null, saving = false, refreshAgain = false;
  let sourceEpoch = 0, returnFocus, contextError = '', activeTab = 'research';
  const dismissed = new Set(), seenJobs = new Map();
  const drafts = new Map();
  const draftKey = memory => 'research.companion.draft.'+scope+':'+(memory?.id || 'new');
  function stashDraft() {
    if(!editing || !$('rn-text'))return;
    const value={...editing,text:$('rn-text').value},key=draftKey(editing.memory),serialized=JSON.stringify(value);
    if(JSON.stringify(drafts.get(key))===serialized)return;
    drafts.set(key,value);try{localStorage.setItem(key,serialized);}catch{}
  }
  function readDraft(memory) {const key=draftKey(memory);try{return drafts.get(key) || JSON.parse(localStorage.getItem(key) || 'null');}catch{return drafts.get(key);}}
  function clearDraft() {const key=draftKey(editing?.memory);drafts.delete(key);try{localStorage.removeItem(key);}catch{}}
  const stepId = () => snapshot.currentStepId || snapshot.problem?.steps?.[snapshot.progress?.cur_step]?.id || '';
  const base = () => '/api/solve/sessions/' + enc(snapshot.id);
  const query = () => '?pid=' + enc(pid()) + '&step_id=' + enc(stepId());
  const busyInput = () => {
    const field=document.activeElement;
    return !!field?.closest('.CodeMirror,[contenteditable=true]') || !!field?.matches('textarea,input') && !!field.value?.trim();
  };
  const icon = name => window.ResearchUI?.icon(name) || (name === 'close' ? '&#215;' : '');
  const clip = (text, length = 86) => String(text || '').replace(/\s+/g, ' ').slice(0,length) + (String(text || '').length > length ? '…' : '');
  async function request(url, method = 'GET', body, signal) {
    const response = await fetch(url, {method,signal,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result = await response.json().catch(()=>({}));
    if(!response.ok){const error = new Error(response.status===409 ? '已有更新，你写的内容还在。先看最新记录，再保存。' : response.status===404 ? '这份记录暂时不可用，原始研究材料仍可从项目查看。' : typeof result.detail==='string' ? result.detail : '暂时没有连上，稍后再试。');error.status=response.status;throw error;}
    return result;
  }
  function message(text) {if($('rn-message'))$('rn-message').textContent=text;}
  function mount() {
    if(document.documentElement.dataset.guidePreview)return;
    const bar=document.querySelector('.studio-context-bar');
    if(!bar || $('rn-entry'))return;
    const entry=document.createElement('button');entry.id='rn-entry';entry.className='rn-entry';entry.type='button';entry.textContent='研究手记';entry.onclick=open;bar.append(entry);
    const margin=document.createElement('aside');margin.id='rn-margin';margin.className='rn-margin';margin.hidden=true;margin.setAttribute('aria-label','研究批注');
    margin.innerHTML='<img width="32" height="38" alt=""><div><span id="rn-margin-role"></span><p id="rn-margin-text"></p></div><button type="button" id="rn-margin-source" class="rn-link">查看出处</button><button type="button" id="rn-dismiss" class="rn-icon" title="收起本条批注" aria-label="收起本条批注">'+icon('close')+'</button>';
    bar.after(margin);
    $('rn-dismiss').onclick=()=>{const key=margin.dataset.key;dismissed.add(key);try{sessionStorage.setItem('research.note.dismissed.'+key,'1');}catch{}margin.hidden=true;};
    const dialog=document.createElement('dialog');dialog.id='rn-dialog';dialog.className='rn-dialog';dialog.setAttribute('aria-labelledby','rn-title');
    dialog.innerHTML=`<header class="rn-head"><div><span class="rn-eyebrow" id="rn-project"></span><h2 id="rn-title">研究手记</h2></div><button type="button" class="rn-icon" id="rn-close" aria-label="关闭研究手记" title="关闭">${icon('close')}</button></header>
      <nav class="rn-tabs" aria-label="研究手记视图"><button type="button" data-tab="research" aria-pressed="true">接着研究</button><button type="button" data-tab="preferences" aria-pressed="false">回应偏好</button></nav>
      <div class="rn-scroll"><section id="rn-research"><div class="rn-intro"><div class="rn-portraits"><img src="/static/guide-alpha-calm.webp" width="50" height="64" alt="阿尔法"><img src="/static/guide-xiaobei-curious.webp" width="48" height="44" alt="小北"></div><div><h3 id="rn-step">当前研究</h3><p>先留下问题，再寻找依据。</p></div></div><div id="rn-records"></div><section class="rn-memory"><div class="rn-section-title"><h3>留给下次的问题</h3><button type="button" id="rn-add" class="rn-link">记下一条</button></div><div id="rn-memories"></div><form id="rn-form" hidden><label for="rn-text">我想记住</label><textarea id="rn-text" maxlength="1200" rows="3" required></textarea><div class="rn-form-actions"><span id="rn-edit-source"></span><button type="button" id="rn-cancel" class="rn-link">取消</button><button type="submit" id="rn-save" class="rn-primary">保存问题</button></div></form></section></section>
      <section id="rn-preferences" hidden><h3>讨论时，我更习惯</h3><form id="rn-preference-form"><fieldset><legend>回应详略</legend><div class="rn-options"><label><input type="radio" name="detail" value="concise">简洁一些</label><label><input type="radio" name="detail" value="balanced" checked>按问题展开</label><label><input type="radio" name="detail" value="detailed">保留推导</label></div></fieldset><label class="rn-setting"><span>解释概念时带一个具体例子</span><input type="checkbox" id="rn-examples"></label><button class="rn-primary" type="submit">保存回应偏好</button></form><p class="rn-caption">用于当前档案的后续讨论。本次消息中的要求优先。</p><section class="rn-local-settings"><h3>陪伴与声音</h3><label class="rn-setting"><span>显示研究批注</span><input type="checkbox" id="rn-enabled"></label><label class="rn-setting"><span>短音效</span><input type="checkbox" id="rn-sound"></label><div class="rn-sound-list"><span>结果落定</span><span>两枚轻玻璃音</span><button type="button" data-sound="result" aria-label="试听结果音" title="试听结果音">▷</button><span>确认判断</span><span>短促木音</span><button type="button" data-sound="confirmed" aria-label="试听确认音" title="试听确认音">▷</button><span>翻开手记</span><span>轻翻纸声</span><button type="button" data-sound="review" aria-label="试听翻页音" title="试听翻页音">▷</button><span>需要检查</span><span>柔和低音</span><button type="button" data-sound="failed" aria-label="试听提示音" title="试听提示音">▷</button></div></section></section>
      <section id="rn-source" hidden><button type="button" class="rn-link" id="rn-source-back">返回手记</button><p class="rn-eyebrow" id="rn-source-label"></p><h3 id="rn-source-title">原始记录</h3><div id="rn-source-content" class="rn-source-content"></div><button type="button" class="rn-link" id="rn-source-open" hidden>打开相关研究</button></section><p id="rn-message" role="status" aria-live="polite"></p></div>`;
    document.body.append(dialog);
    $('rn-close').onclick=()=>dialog.close();
    dialog.addEventListener('close',()=>{sourceEpoch++;if(returnFocus?.isConnected && returnFocus.getClientRects().length)returnFocus.focus({preventScroll:true});});
    dialog.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>tab(button.dataset.tab));
    $('rn-add').onclick=()=>editMemory();$('rn-cancel').onclick=()=>{stashDraft();editing=null;$('rn-form').hidden=true;};
    $('rn-text').oninput=stashDraft;
    $('rn-form').onsubmit=saveMemory;$('rn-preference-form').onsubmit=saveResponsePreferences;
    $('rn-enabled').onchange=()=>window.ResearchCompanion.setPreferences({enabled:$('rn-enabled').checked});
    $('rn-sound').onchange=()=>window.StudioPersonality?.setSound($('rn-sound').checked);
    dialog.querySelectorAll('[data-sound]').forEach(button=>button.onclick=async event=>{const played=await window.StudioPersonality?.preview(button.dataset.sound,event);message(played?'': '声音暂时未播放，请稍后再试。');});
    $('rn-source-back').onclick=()=>tab('research');
    paint();
  }
  function tab(name) {
    activeTab=name;sourceEpoch++;
    for(const id of ['research','preferences','source'])$('rn-'+id).hidden=id!==name;
    $('rn-dialog').querySelectorAll('[data-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tab===name)));
    message('');
  }
  function open() {
    mount();if(!$('rn-dialog'))return;
    returnFocus=document.activeElement;
    if(!$('rn-dialog').open){$('rn-dialog').showModal();$('rn-close').focus();window.StudioPersonality?.play('review');}
    $('rn-enabled').checked=prefs.enabled!==false;
    $('rn-sound').checked=!!window.StudioPersonality?.soundEnabled;
    if(context)fillPreferences();
    refresh(true);
  }
  function fillPreferences() {
    if(!$('rn-preference-form') || $('rn-preference-form').dataset.dirty==='true')return;
    const preferences=context?.preferences || {};
    $('rn-preference-form').querySelector(`[value="${['concise','detailed'].includes(preferences.detail)?preferences.detail:'balanced'}"]`).checked=true;
    $('rn-examples').checked=!!preferences.examples;
    $('rn-preference-form').onchange=()=>{$('rn-preference-form').dataset.dirty='true';};
  }
  function update(next, nextState, nextPrefs) {
    if(document.documentElement.dataset.guidePreview)return;
    stashDraft();snapshot=next || {};state=nextState || {};prefs=nextPrefs || {};
    const nextScope=[pid(),snapshot.id,stepId()].join(':');
    if(scope!==nextScope){
      if(scope.split(':')[0]!==pid() && $('rn-preference-form'))$('rn-preference-form').dataset.dirty='false';
      scope=nextScope;epoch++;controller?.abort();pending=null;context=null;contextError='';signature='';editing=null;seenJobs.clear();
      if($('rn-form'))$('rn-form').hidden=true;if($('rn-source'))tab('research');paintRecords();
    }
    mount();paint();
    const nextSignature=[scope,snapshot.updated_at,snapshot.revision,JSON.stringify(snapshot.outcomes || {}),(snapshot.evidence || []).map(e=>e.id+':'+e.stale).join(',')].join('|');
    if(nextSignature!==signature){signature=nextSignature;scheduleRefresh();}
  }
  function scheduleRefresh() {clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh(),220);}
  async function refresh(force=false) {
    if(!snapshot.id || !pid())return;
    if(pending){refreshAgain=true;return pending;}
    const ownEpoch=epoch;controller=new AbortController();
    pending=request(base()+'/companion-context'+query(),'GET',undefined,controller.signal).then(value=>{
      if(ownEpoch!==epoch)return;context=value;contextError='';paint();paintRecords();fillPreferences();
    }).catch(error=>{if(ownEpoch!==epoch || error.name==='AbortError')return;contextError='手记暂时没有连上，讨论与原始研究记录仍可使用。';if(force)message(contextError);paintRecords();}).finally(()=>{if(ownEpoch===epoch){pending=null;if(refreshAgain){refreshAgain=false;scheduleRefresh();}}});
    return pending;
  }
  function chosen() {
    const records=context?.records || [];
    const review=records.find(record=>record.needs_review);
    if(review)return {role:'阿尔法',text:'条件已经变了，先复核这条依据：'+clip(review.text,58),record:review,key:review.id+':'+context.revision};
    if(['reading','thinking','running'].includes(state.state))return {role:'阿尔法',text:state.alpha,key:state.state};
    const memory=context?.memories?.find(item=>item.step_id===stepId()) || context?.memories?.[0];
    if(memory)return {role:'小北',text:'你留下的这个问题，还在这里：'+clip(memory.text,62),memory,key:memory.id+':'+memory.revision};
    const question=records.find(record=>/question|unresolved/.test(record.kind));
    if(question)return {role:'小北',text:'接着这个还没回答的问题：'+clip(question.text,62),record:question,key:question.id};
    const decision=records.find(record=>record.source?.kind==='outcome' && !record.title.includes('待审'));
    if(decision)return {role:'阿尔法',text:'接着已确认的判断，留意它的适用条件：'+clip(decision.text,56),record:decision,key:decision.id};
    const thought=records.find(record=>record.source?.kind==='initial_thought');
    if(thought)return {role:'小北',text:'你的出发点还在：'+clip(thought.text,70),record:thought,key:thought.id};
    return null;
  }
  function paint() {
    const margin=$('rn-margin');if(!margin)return;
    $('rn-entry').disabled=!snapshot.id;
    const note=chosen(), key=scope+':'+(note?.key || '');
    let wasDismissed=dismissed.has(key);try{wasDismissed ||= sessionStorage.getItem('research.note.dismissed.'+key)==='1';}catch{}
    if(prefs.enabled===false || !note || wasDismissed){margin.hidden=true;return;}
    // A late context response never replaces text while the student is composing.
    if(busyInput() || document.hidden){clearTimeout(paintTimer);paintTimer=setTimeout(paint,1200);return;}
    margin.dataset.key=key;margin.hidden=false;
    const role=note.role==='阿尔法'?'alpha-calm':'xiaobei-curious';margin.querySelector('img').src='/static/guide-'+role+'.webp';
    $('rn-margin-role').textContent=note.role;$('rn-margin-text').textContent=note.text;
    const link=$('rn-margin-source');link.textContent=note.record?'查看出处':'打开手记';link.onclick=()=>{open();if(note.record)showSource(note.record);};
  }
  function paintRecords() {
    if(!$('rn-records'))return;
    $('rn-project').textContent=clip(snapshot.title || snapshot.problem?.title || '我的研究',64);
    $('rn-step').textContent=context?.step_title || snapshot.problem?.steps?.find(step=>step.id===stepId())?.title || '当前研究';
    const records=context?.records || [];
    $('rn-records').innerHTML=contextError?'<p class="rn-empty">'+esc(contextError)+'</p><button type="button" class="rn-link" data-retry>重试读取</button>':!context?'<p class="rn-empty">正在翻开研究记录…</p>':records.length?records.slice(0,5).map((record,index)=>`<article class="rn-record"><div class="rn-record-meta"><span>${record.needs_review?'阿尔法 · 先核对条件':record.source?.kind==='initial_thought'?'小北 · 记得你的出发点':/question|unresolved/.test(record.kind)?'小北 · 还有一个问题':'阿尔法 · 留下依据'}</span><span>${esc(record.source_label || '项目记录')}</span></div><h4>${esc(record.title)}</h4><p>${esc(clip(record.text,210))}</p><div class="rn-record-actions"><button type="button" class="rn-link" data-source="${index}">查看出处</button><button type="button" class="rn-link" data-remember="${index}">留个问题</button></div></article>`).join(''):'<p class="rn-empty">还没有留下研究记录。先说说你的想法。</p>';
    $('rn-records').querySelector('[data-retry]')?.addEventListener('click',()=>refresh(true));
    $('rn-records').querySelectorAll('[data-source]').forEach(button=>button.onclick=()=>showSource(records[Number(button.dataset.source)]));
    $('rn-records').querySelectorAll('[data-remember]').forEach(button=>button.onclick=()=>editMemory(null,records[Number(button.dataset.remember)]));
    const memories=context?.memories || [];
    $('rn-memories').innerHTML=memories.length?memories.map((memory,index)=>`<article class="rn-saved-note"><p>${esc(memory.text)}</p><div><span>${memory.source?'<button type="button" class="rn-link" data-memory-source="'+index+'">查看出处</button>':'你亲手记下'}</span><button type="button" class="rn-link" data-edit="${index}">编辑</button><button type="button" class="rn-link" data-delete="${index}">忘记这条</button></div></article>`).join(''):'<p class="rn-empty">还没有问题留在这里。</p>';
    $('rn-memories').querySelectorAll('[data-memory-source]').forEach(button=>button.onclick=()=>showSource({source:memories[Number(button.dataset.memorySource)].source,title:'问题对应的研究记录',source_label:'研究出处'}));
    $('rn-memories').querySelectorAll('[data-edit]').forEach(button=>button.onclick=()=>editMemory(memories[Number(button.dataset.edit)]));
    $('rn-memories').querySelectorAll('[data-delete]').forEach(button=>button.onclick=()=>deleteMemory(memories[Number(button.dataset.delete)],button));
  }
  function editMemory(memory=null, record=null) {
    if(saving)return;
    stashDraft();const draft=readDraft(memory);
    const resume=draft?.text?.trim() && (draft.memory?.id || '')===(memory?.id || '');
    editing=resume?draft:{memory,source:memory?.source || record?.source || null,operationId:uuid()};
    $('rn-form').hidden=false;$('rn-text').value=resume?draft.text:memory?.text || '';
    $('rn-edit-source').textContent=editing.source?'出处：'+(record?.source_label || '原研究记录'):'';
    if(resume)message('已恢复尚未保存的问题。');
    $('rn-text').focus();$('rn-text').scrollIntoView({block:'nearest'});
  }
  async function saveMemory(event) {
    event.preventDefault();if(saving || !editing || !context)return;
    const text=$('rn-text').value.trim();if(!text)return;
    const draft=editing, ownEpoch=epoch;
    saving=true;$('rn-save').disabled=true;message('');
    try{
      const data=draft.memory?{pid:pid(),text,expected_revision:draft.memory.revision}:{pid:pid(),text,step_id:stepId(),source:draft.source,operation_id:draft.operationId};
      await request(base()+'/companion-memories'+(draft.memory?'/'+enc(draft.memory.id):''),draft.memory?'PATCH':'POST',data);
      if(ownEpoch!==epoch)return;clearDraft();editing=null;$('rn-form').hidden=true;await pending;await refresh(true);message('记下了。下次从这个问题接着想。');
    }catch(error){if(ownEpoch===epoch){message(error.message);if(error.status===409){await pending;await refresh();const latest=context?.memories?.find(item=>item.id===draft.memory?.id);if(latest && editing===draft){draft.memory=latest;message('最新记录：'+latest.text+'。你的输入已保留；再次保存将采用你的编辑。');}}}}
    finally{saving=false;$('rn-save').disabled=false;}
  }
  async function deleteMemory(memory,button) {
    const ownEpoch=epoch;button.disabled=true;
    try{
      await request(base()+'/companion-memories/'+enc(memory.id)+'?pid='+enc(pid())+'&expected_revision='+memory.revision,'DELETE');if(ownEpoch!==epoch)return;
      const key=draftKey(memory);drafts.delete(key);try{localStorage.removeItem(key);}catch{}
      if(editing?.memory?.id===memory.id){editing=null;$('rn-form').hidden=true;}
      await pending;await refresh(true);message('已忘记这条问题，原始研究材料保留。');
    }
    catch(error){if(ownEpoch===epoch){button.disabled=false;message(error.message);}}
  }
  async function saveResponsePreferences(event) {
    event.preventDefault();if(!context)return;
    const form=event.currentTarget,button=form.querySelector('[type=submit]'),ownEpoch=epoch;
    button.disabled=true;
    try{const value=await request('/api/profiles/'+enc(pid())+'/companion-preferences','PATCH',{detail:new FormData(form).get('detail'),examples:$('rn-examples').checked,expected_revision:context.preferences.revision});if(ownEpoch!==epoch)return;context.preferences=value;form.dataset.dirty='false';message('后续讨论会按这个习惯回应。');}
    catch(error){if(ownEpoch===epoch){message(error.message);if(error.status===409)await refresh();}}
    finally{button.disabled=false;}
  }
  async function showSource(record) {
    if(!record?.source)return;
    tab('source');const ownSource=++sourceEpoch,ownEpoch=epoch;
    $('rn-source-label').textContent=record.source_label || '研究出处';$('rn-source-title').textContent=record.title || '原始记录';$('rn-source-content').textContent='正在读取原文…';$('rn-source-open').hidden=true;
    try{
      const source=await request(base()+'/companion-source?pid='+enc(pid())+'&kind='+enc(record.source.kind)+'&source_id='+enc(record.source.id));
      if(ownEpoch!==epoch || ownSource!==sourceEpoch)return;
      $('rn-source-content').textContent=source.text || source.content || '这份原始记录没有正文。';
      const view=record.source.kind==='outcome'?'outcome':record.source.kind==='evidence'?'verification':null;
      $('rn-source-open').hidden=!view;
      $('rn-source-open').onclick=()=>{$('rn-dialog').close();window.dispatchEvent(new CustomEvent('research-companion-action',{detail:{view,stepId:record.source.step_id}}));};
    }catch(error){if(ownEpoch===epoch && ownSource===sourceEpoch)$('rn-source-content').textContent=error.message;}
  }
  window.addEventListener('studio-ready',()=>{mount();paintRecords();});
  window.addEventListener('studio-workflow',event=>{
    if(event.detail?.sessionId!==snapshot.id)return;
    let changed=false;
    for(const job of event.detail.jobs || []){const previous=seenJobs.get(job.id);seenJobs.set(job.id,job.status);if(previous!==job.status && ['completed','failed','stale'].includes(job.status))changed=true;}
    if(changed)scheduleRefresh();
  });
  window.addEventListener('solve-evidence',scheduleRefresh);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){paint();scheduleRefresh();}});
  window.addEventListener('studio-sound-change',()=>{if($('rn-sound'))$('rn-sound').checked=!!window.StudioPersonality?.soundEnabled;});
  window.ResearchNotebook={update,open,refresh};
})();
