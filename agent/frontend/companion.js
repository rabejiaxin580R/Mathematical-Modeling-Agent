/* Shared renderer and event bridge for the Alpha/Xiaobei research companion. */
(function () {
  'use strict';
  if (window.ResearchCompanion) return;
  let model = CompanionState.create(), view = CompanionState.select(model), waitTasks = [];
  const hosts = new Set();
  const animations = new WeakMap();
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const profileId = () => {try{return localStorage.getItem('mm_profile_id') || 'guest';}catch{return 'guest';}};
  const key = () => 'mm.companion.v1.' + profileId();
  function readPreferences() { try { return CompanionState.preferences(JSON.parse(localStorage.getItem(key()))); } catch { return CompanionState.preferences(); } }
  let prefs = readPreferences();
  function savePreferences() { try { localStorage.setItem(key(), JSON.stringify(prefs)); } catch { /* The companion remains usable without storage. */ } }
  const sprite = (role, expression) => `/static/guide-${role}-${expression}.webp`;
  function metrics() {
    const s = model.snapshot, step = (s.problem?.steps || []).find(item => item.id === s.currentStepId) || (s.problem?.steps || [])[s.progress?.cur_step];
    if(s.projectStats)return s.projectStats;
    const evidence = (s.evidence || []).filter(item => item.status === 'success').length;
    const confirmed = Object.values(s.outcomes || {}).filter(item => item.status === 'confirmed').length;
    const revised = Object.values(s.outcomes || {}).filter(item => item.status === 'confirmed' && ['partial','unsupported'].includes(item.verdict)).length;
    return {step: step?.title || '', evidence, confirmed, revised, completed:0};
  }
  function markup() {
    const m = metrics();
    return `<div class="companion-heading"><span class="companion-kicker">研究搭档</span><button class="companion-expand" type="button" aria-expanded="false" aria-label="展开研究搭档状态" title="展开研究搭档状态">⌄</button></div>
      <div class="companion-pair">
        <img class="companion-alpha" src="${sprite('alpha', view.alphaExpression)}" alt="" width="72" height="84">
        <img class="companion-bei" src="${sprite('xiaobei', view.beiExpression)}" alt="" width="64" height="58">
      </div>
      <div class="companion-copy"><strong data-title>${esc(view.title)}</strong><p><b>阿尔法</b><span data-alpha>${esc(view.alpha)}</span></p><p><b>小北</b><span data-bei>${esc(view.xiaobei)}</span></p><button class="companion-action" type="button" hidden></button></div>
      <details class="companion-wait" hidden><summary>任务详情</summary><ul></ul><button type="button" class="companion-cancel" hidden>停止生成</button></details>
      <dl class="companion-record" aria-label="共同研究记录"><div><dt>成功验证</dt><dd data-count="evidence">${m.evidence}</dd></div><div><dt>确认成果</dt><dd data-count="confirmed">${m.confirmed}</dd></div><div><dt>修正想法</dt><dd data-count="revised">${m.revised}</dd></div><div data-completed><dt>完成项目</dt><dd data-count="completed">${m.completed}</dd></div></dl>`;
  }
  const setText = (node,text) => {if(node && node.textContent!==String(text))node.textContent=text;};
  function place(host) {
    if(location.pathname!=='/solve')return;
    const cockpit=document.getElementById('sv-cockpit'), wide=matchMedia('(min-width:1280px)').matches;
    const rail=wide && cockpit && !cockpit.hidden;
    host.classList.toggle('companion-rail',!!rail);host.classList.toggle('companion-strip',!rail);
    if(rail && host.parentNode!==cockpit)cockpit.append(host);
    if(!rail && document.getElementById('sv-tabs')?.nextElementSibling!==host)document.getElementById('sv-tabs')?.after(host);
    document.body.classList.toggle('has-research-companion',!!rail && prefs.enabled);
  }
  function render(forceAnnouncement = false) {
    if(!document.body)return;
    let next = CompanionState.select(model);
    if(model.snapshot.mode==='home' && !Object.keys(model.operations).length){next={...next,...CompanionState.handoff(model.snapshot),title:'接着上次的研究',action:null};}
    if(waitTasks.length && ['idle','confirmed','complete','draft_ready'].includes(next.state)){
      const label=waitTasks[0].label,state=/读题/.test(label)?'reading':/代码/.test(label)?'running':'thinking';
      next=CompanionState.select(CompanionState.reduce(model,'start',{id:'wait-view',state}));
      next={...next,alpha:`正在${label}。结果会在原操作区域显示。`};
    }
    const changed = next.state !== view.state || next.alpha !== view.alpha || next.xiaobei !== view.xiaobei;
    view = next;
    if(location.pathname==='/solve' && window.ResearchNotebook){
      window.ResearchNotebook.update(model.snapshot, view, prefs);
      return;
    }
    for (const host of [...hosts]) {
      if (!host.isConnected) { hosts.delete(host); continue; }
      place(host);
      host.hidden = !prefs.enabled || (host.dataset.transient==='true' && !waitTasks.length && !Object.keys(model.operations).length && view.state==='idle');
      if(host.hidden){host.querySelectorAll('img').forEach(img=>animations.get(img)?.cancel());continue;}
      host.dataset.state = view.state;
      host.dataset.motion = prefs.motion;
      const expanded = host.classList.contains('is-expanded');
      if(!host.querySelector('.companion-copy')){
        host.innerHTML=markup();
        host.querySelector('.companion-expand').onclick=()=>{host.classList.toggle('is-expanded');if(host.classList.contains('is-expanded')){prefs.read=[model.snapshot.id || location.pathname,view.state,model.snapshot.revision || 0].join(':');savePreferences();}render();};
        host.querySelector('.companion-action').onclick=()=>window.dispatchEvent(new CustomEvent('research-companion-action',{detail:view.action}));
        host.querySelectorAll('img').forEach(img=>img.onerror=()=>{img.hidden=true;host.classList.add('has-missing-image');});
      }
      const button = host.querySelector('.companion-expand');
      button.setAttribute('aria-expanded', String(expanded));
      button.textContent = expanded ? '⌃' : '⌄';
      button.setAttribute('aria-label',expanded?'收起研究搭档状态':'展开研究搭档状态');
      setText(host.querySelector('[data-title]'),view.title);setText(host.querySelector('[data-alpha]'),view.alpha);setText(host.querySelector('[data-bei]'),view.xiaobei);
      for(const [selector,role,expression] of [['.companion-alpha','alpha',view.alphaExpression],['.companion-bei','xiaobei',view.beiExpression]]){
        const img=host.querySelector(selector),src=sprite(role,expression);
        if(img.getAttribute('src')!==src){img.hidden=false;img.src=src;animations.get(img)?.cancel();if(prefs.motion!=='off'&&!reducedMotion.matches)animations.set(img,img.animate([{opacity:.3},{opacity:1}],{duration:180}));}
        if(prefs.motion==='off'||reducedMotion.matches)animations.get(img)?.cancel();
      }
      const action = host.querySelector('.companion-action');
      action.hidden=!view.action || location.pathname!=='/solve';setText(action,view.action?.label || '');
      const counts=metrics();host.querySelectorAll('[data-count]').forEach(node=>setText(node,counts[node.dataset.count] || 0));host.querySelector('[data-completed]').hidden=model.snapshot.mode!=='home';
      const waiting=host.querySelector('.companion-wait');waiting.hidden=!waitTasks.length && !Object.keys(model.operations).length;
      const rows=waitTasks.map(task=>`${task.label} · ${task.seconds} 秒`);
      if(!rows.length)rows.push(...Object.values(model.operations).map(op=>op.label || view.title));
      const list=waiting.querySelector('ul');if(list.textContent!==rows.join('')){list.replaceChildren(...rows.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));}
      const cancel=Object.values(model.operations).find(op=>typeof op.cancel==='function');const stop=waiting.querySelector('button');stop.hidden=!cancel;stop.onclick=()=>cancel?.cancel();
      host.setAttribute('aria-label','研究搭档');
    }
    if ((changed || forceAnnouncement) && prefs.enabled) {
      let live = document.getElementById('research-companion-live');
      if (!live) { live=document.createElement('p');live.id='research-companion-live';live.className='companion-sr';live.setAttribute('role','status');live.setAttribute('aria-live','polite');live.setAttribute('aria-atomic','true');document.body.append(live); }
      live.textContent = `${view.title}。阿尔法：${view.alpha} 小北：${view.xiaobei}`;
    }
  }
  function mount(target) {
    const nodes = typeof target === 'string' ? document.querySelectorAll(target) : [target];
    for (const node of nodes) if (node) {node.classList.add('research-companion');node.setAttribute('role','group');hosts.add(node);}
    render(); return api;
  }
  function sync(snapshot) { model = CompanionState.reduce(model, 'sync', snapshot || {}); render(); return view; }
  function notify(type, payload = {}) {
    const mapped = ['start','finish','result','acknowledge','reset'].includes(type) ? type : 'result';
    model = CompanionState.reduce(model, mapped, mapped === type ? payload : {state:type, ...payload});render();return view;
  }
  function setPreferences(next) { prefs = CompanionState.preferences({...prefs, ...next});savePreferences();render();document.querySelectorAll('.companion-settings').forEach(section=>{section.querySelector('input').checked=prefs.enabled;section.querySelectorAll('[data-motion]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.motion===prefs.motion)));});if(!prefs.enabled){const live=document.getElementById('research-companion-live');if(live)live.textContent='';}return prefs; }
  function addSettings(container) {
    if (!container || container.querySelector('.companion-settings')) return;
    const section=document.createElement('section');section.className='companion-settings';
    section.innerHTML='<label><span>搭档反馈</span><input type="checkbox"></label><fieldset><legend>动态效果</legend><div class="ui-segment"><button type="button" data-motion="system">跟随系统</button><button type="button" data-motion="on">开</button><button type="button" data-motion="off">关</button></div></fieldset>';
    const toggle=section.querySelector('input');toggle.checked=prefs.enabled;toggle.onchange=()=>setPreferences({enabled:toggle.checked});
    section.querySelectorAll('[data-motion]').forEach(button=>{button.setAttribute('aria-pressed',String(button.dataset.motion===prefs.motion));button.onclick=()=>{setPreferences({motion:button.dataset.motion});section.querySelectorAll('[data-motion]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));};});
    container.append(section);
  }
  window.addEventListener('research-companion', event => notify(event.detail.type, event.detail));
  window.addEventListener('storage', event => {if(event.key===key() || event.key==='mm_profile_id'){prefs=readPreferences();if(event.key==='mm_profile_id')model=CompanionState.create();render();}});
  reducedMotion.addEventListener('change',()=>render());
  const api = window.ResearchCompanion = {mount,sync,notify,setPreferences,addSettings,openNotebook:()=>window.ResearchNotebook?.open(),get state(){return view;},get preferences(){return {...prefs};},handoff:CompanionState.handoff,totals:CompanionState.totals};
  function boot(){
    if(document.documentElement.dataset.guidePreview)return;
    // The quiet home keeps the project and current tasks in focus. Settings remain
    // available through the shared shell; no hidden duplicate live announcement.
    if(location.pathname==='/home' && document.body.dataset.homeLayout==='quiet')return;
    if(location.pathname==='/solve' && window.ResearchNotebook){
      addSettings(document.querySelector('.ui-header .ui-menu-items'));
      window.MMWait?.subscribe(tasks=>{waitTasks=tasks;render();});
      render();return;
    }
    const host=document.createElement('aside');host.id='research-companion';
    if(location.pathname==='/solve'){document.getElementById('sv-tabs')?.after(host);matchMedia('(min-width:1280px)').addEventListener('change',()=>render());}
    else if(location.pathname==='/home'){host.className='companion-home';document.getElementById('home-feature')?.after(host);}
    else{host.className='companion-strip';host.dataset.transient='true';(document.querySelector('.ui-header') || document.body.firstElementChild)?.after(host);}
    mount(host);addSettings(document.querySelector('.ui-header .ui-menu-items'));
    window.MMWait?.subscribe(tasks=>{waitTasks=tasks;render();});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
