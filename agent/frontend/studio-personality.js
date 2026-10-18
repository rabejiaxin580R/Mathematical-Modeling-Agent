/* Solve's small finishing touches. Does not own the tour, editor or research state. */
(function () {
  'use strict';
  if (document.documentElement.dataset.guidePreview) return;
  const $ = id => document.getElementById(id);
  const preferenceKey = () => {
    let profile='guest';try{profile=localStorage.getItem('mm_profile_id') || profile;}catch{}
    return 'research.studio.sound.' + profile;
  };
  let enabled = false, audio, unlocked = false, lastSound = -Infinity, mounted = false;
  let workflowSession = '', workflowStep = '', previousJobs = new Map(), lastStage = '';
  const voices = new Set();
  const soundKinds = new Set(['result','confirmed','review','failed']);
  const guideActive = () => !!document.querySelector('.guide-shell[open],.guide-intro[open]');
  function readPreference() {
    try { enabled = localStorage.getItem(preferenceKey()) === 'on'; } catch { enabled = false; }
  }
  function stopSound() {
    for (const voice of voices) { try { voice.stop(); } catch {} }
    voices.clear();
  }
  function paintSound() {
    const button = $('studio-sound'); if (!button) return;
    button.setAttribute('aria-pressed', String(enabled));
    button.setAttribute('aria-label', enabled ? '音效已开启，点击静音' : '音效已静音，点击开启');
    button.title = enabled ? '短音效已开启 · 点击静音' : '开启短音效';
    button.querySelector('span').textContent = enabled ? '音效开' : '静音';
    button.dataset.muted = String(!enabled);
  }
  async function unlock(event, audition = false) {
    if (!event?.isTrusted || (!enabled && !audition) || document.hidden || guideActive()) return false;
    try {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) return false;
      audio ||= new Context();
      if (audio.state === 'suspended') await audio.resume();
      unlocked = audio.state === 'running';
    } catch { unlocked = false; }
    return unlocked;
  }
  // Shared scheduling also lets OfflineAudioContext render the exact audition samples.
  function synthesize(context, kind, register = () => {}) {
    if(!soundKinds.has(kind))return false;
    const start=context.currentTime;
    const envelope=(source,level,delay,duration,attack,filters=[])=>{
      const gain=context.createGain(), at=start+delay;
      gain.gain.setValueAtTime(0,at);gain.gain.linearRampToValueAtTime(level,at+attack);
      gain.gain.exponentialRampToValueAtTime(.00001,at+duration-.006);
      gain.gain.linearRampToValueAtTime(0,at+duration);
      const nodes=[source,...filters,gain];
      for(let index=0;index<nodes.length-1;index++)nodes[index].connect(nodes[index+1]);
      gain.connect(context.destination);register(source);
      source.onended=()=>{voices.delete(source);for(const node of nodes)try{node.disconnect();}catch{}};
      source.start(at);source.stop(at+duration);
    };
    const tone=(frequency,level,delay,duration,type='sine')=>{
      const source=context.createOscillator();source.type=type;source.frequency.setValueAtTime(frequency,start+delay);
      envelope(source,level,delay,duration,.006);
    };
    const noise=(duration,level,frequency,q,attack)=>{
      const buffer=context.createBuffer(1,Math.ceil(context.sampleRate*duration),context.sampleRate);
      const data=buffer.getChannelData(0);let seed=9173;
      for(let index=0;index<data.length;index++){seed=(Math.imul(seed,1664525)+1013904223)>>>0;data[index]=seed/2147483648-1;}
      const source=context.createBufferSource();source.buffer=buffer;
      const filter=context.createBiquadFilter();filter.type='bandpass';
      filter.frequency.setValueAtTime(frequency,start);filter.Q.setValueAtTime(q,start);
      envelope(source,level,0,duration,attack,[filter]);
    };
    if(kind==='result'){
      for(const [frequency,delay,duration] of [[587.3295,0,.13],[739.9888,.10,.14]]){
        tone(frequency,.028,delay,duration);tone(frequency*2.01,.004,delay,duration*.7);
      }
    }else if(kind==='confirmed'){
      noise(.032,.03,750,1,.003);tone(440,.025,0,.075,'triangle');tone(587.3295,.013,.012,.168);
    }else if(kind==='review')noise(.12,.055,1800,.65,.018);
    else tone(293.6648,.035,0,.10);
    return true;
  }
  function playSound(kind, audition = false) {
    if (!soundKinds.has(kind) || (!enabled && !audition) || !unlocked || !audio || audio.state !== 'running' || document.hidden || guideActive()) return false;
    if (performance.now() - lastSound < (audition ? 350 : 1200)) return false;
    try {
      synthesize(audio,kind,voice=>voices.add(voice));lastSound=performance.now();return true;
    } catch { stopSound(); return false; }
  }
  const play = kind => playSound(kind);
  async function preview(kind, trustedEvent) {
    if(!soundKinds.has(kind) || !trustedEvent?.isTrusted || trustedEvent.type!=='click')return false;
    if(!await unlock(trustedEvent,true))return false;
    return playSound(kind,true);
  }
  function setSound(value) {
    enabled = !!value;
    try { localStorage.setItem(preferenceKey(), enabled ? 'on' : 'off'); } catch {}
    if (!enabled) stopSound();
    paintSound();
  }
  function element(tag, className, text) {
    const node = document.createElement(tag); if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function downloadCard(material) {
    const fields = [['我的原始想法',material.initialThought],['本步决定',material.decision],['为什么这样做',material.reason],['依据与结果',material.basis],['适用条件与限制',material.limits]];
    const text = ['# '+(material.projectTitle || '我的研究'), '', '## '+material.title,
      '', '状态：'+(material.confirmed ? '学生已确认' : '待确认草稿')+' · 第 '+(material.version || 1)+' 版',
      ...fields.flatMap(([title,value])=>['','### '+title,'',String(value || '尚未填写')]),
      '', '来自建模研究台 · 保留想法，也保留依据。', ''].join('\n');
    const url = URL.createObjectURL(new Blob([text],{type:'text/markdown;charset=utf-8'}));
    const link = document.createElement('a'); link.href = url; link.download = '我的研究卡.md'; link.click();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function showCard(material = window.SolveFlow?.currentMaterial?.()) {
    if (guideActive() || !material?.sessionId || document.querySelector('.studio-card-dialog[open]')) return;
    const origin = document.activeElement, dialog = element('dialog','studio-card-dialog');
    const header = element('header','studio-card-toolbar');
    const heading = element('h2','', '把这一步，带走。'); heading.id = 'studio-card-heading';
    dialog.setAttribute('aria-labelledby', heading.id);
    const close = element('button','sv-btn','关闭'); close.type = 'button'; close.onclick = () => dialog.close();
    header.append(heading,close); dialog.append(header);
    const card = element('article','studio-research-card');
    card.append(element('p','studio-card-kicker','建模研究台 / 我的研究卡'));
    card.append(element('p','studio-card-project',material.projectTitle || '我的研究'));
    card.append(element('h3','',material.title || '本步成果'));
    card.append(element('p','studio-card-meta',(material.confirmed ? '学生已确认' : '待确认草稿')+' · 第 '+(material.version || 1)+' 版 · '+new Date().toLocaleDateString('zh-CN')));
    for (const [label,value] of [['最初，我这样想',material.initialThought],['这一步，我决定',material.decision],['理由',material.reason],['依据与结果',material.basis],['边界与待验证',material.limits]]) {
      const section = element('section'); section.append(element('h4','',label),element('p','',value || '尚未填写')); card.append(section);
    }
    card.append(element('footer','','想法是你的。让每一步，都有迹可循。'));
    const actions = element('div','studio-card-actions');
    const print = element('button','sv-btn primary','打印 / 存为 PDF'); print.type = 'button';
    print.onclick = () => {
      document.body.classList.add('studio-printing');
      try { window.print(); } finally { document.body.classList.remove('studio-printing'); }
    };
    const save = element('button','sv-btn','下载文字'); save.type = 'button'; save.onclick = () => downloadCard(material);
    actions.append(print,save); dialog.append(card,actions);
    dialog.addEventListener('click',event=>{if(event.target===dialog)dialog.close();});
    dialog.addEventListener('close',()=>{document.body.classList.remove('studio-printing');dialog.remove();if(origin?.isConnected)origin.focus({preventScroll:true});});
    document.body.append(dialog); dialog.showModal(); close.focus();
  }
  function onStage(event) {
    const value = event.detail || {}; if (!value.confirmed || !value.sessionId || guideActive()) return;
    const key = [value.sessionId,value.stepId,value.version].join(':'); if (key===lastStage) return; lastStage=key;
    boot(); const area = $('studio-feedback'); if (!area) return;
    area.replaceChildren(); area.hidden=false;
    const text = element('div'); text.append(element('strong','','这一页，留下了你的判断。'),element('p','','决定、依据和限制已收好。下一步，按你的节奏来。'));
    const view = element('button','sv-btn','带走成果卡'); view.type='button';view.onclick=()=>showCard(value);
    const dismiss = element('button','studio-feedback-close','×');dismiss.type='button';dismiss.setAttribute('aria-label','收起阶段完成提示');dismiss.onclick=()=>{area.hidden=true;};
    area.append(text,view,dismiss); play('confirmed');
  }
  function onWorkflow(event) {
    const value = event.detail || {}, jobs = value.jobs || [];
    if (workflowSession!==value.sessionId || workflowStep!==value.stepId) {
      const feedback=$('studio-feedback'); if(feedback)feedback.hidden=true; workflowStep=value.stepId;
    }
    if (workflowSession!==value.sessionId || value.initial) { workflowSession=value.sessionId;previousJobs=new Map(jobs.map(j=>[j.id,j.status])); return; }
    const changedTo=status=>jobs.some(j=>j.status===status&&['pending','queued','running'].includes(previousJobs.get(j.id)));
    const failed=changedTo('failed'), finished=changedTo('completed');
    previousJobs=new Map(jobs.map(j=>[j.id,j.status]));
    if(failed)play('failed');else if(finished)play('result');
  }
  function boot() {
    const slot=$('studio-personality-slot') || $('wf-other-tools'); if (!slot || mounted) return;
    mounted=true;readPreference();
    const sound=element('button','studio-sound');sound.id='studio-sound';sound.type='button';
    sound.innerHTML='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M4 9h4l5-4v14l-5-4H4zM17 8c2 2 2 6 0 8"/><path class="studio-sound-slash" d="m4 3 16 18"/></svg><span></span>';
    sound.onclick=async event=>{setSound(!enabled);if(enabled)await unlock(event);}; slot.append(sound);paintSound();
    const card=element('button','sv-btn','我的研究卡');card.id='studio-open-card';card.type='button';card.onclick=()=>{
      const value=window.SolveFlow?.currentMaterial?.();
      if(value?.sessionId)showCard(value);else {const notice=$('wf-notice');if(notice)notice.textContent='先打开一个项目，我们就能把思路整理成研究卡。';}
    };$('wf-other-tools')?.append(card);
    const area=element('section','studio-feedback');area.id='studio-feedback';area.hidden=true;area.setAttribute('role','status');
    const contextBar=document.querySelector('.studio-context-bar');
    if(contextBar)contextBar.after(area);else document.querySelector('.sv-chat')?.before(area);
  }
  readPreference();
  window.StudioPersonality={showCard,setSound,play,preview,get soundEnabled(){return enabled;}};
  document.addEventListener('pointerdown',unlock,{passive:true});document.addEventListener('keydown',unlock);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopSound();});
  window.addEventListener('pagehide',stopSound);
  window.addEventListener('storage',event=>{if(event.key===preferenceKey()||event.key==='mm_profile_id'){stopSound();readPreference();paintSound();}});
  window.addEventListener('studio-stage',onStage);window.addEventListener('studio-workflow',onWorkflow);
  window.addEventListener('studio-ready',boot);
  // The tour owns its own voice and preferences. Workbench sounds simply yield.
  const observer=new MutationObserver(()=>{boot();if(guideActive())stopSound();});
  function watch(){observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['open']});boot();}
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',watch,{once:true});else watch();
})();
