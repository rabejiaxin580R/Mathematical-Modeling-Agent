/* Persistent, per-profile tours. Production startup code never runs in previews. */
(function () {
  'use strict';
  if (window.OnboardingTour || document.documentElement.dataset.guidePreview) return;
  const BASE = '/static/', scripts = new Map(), sources = new Map();
  let content, audioManifest, state, service, profileId, readyPromise;
  let shell, frame, block, step, originFocus, originScroll, callback;
  let mode = '', lineIndex = 0, voice, timer, token = 0, sceneToken = 0, paused = false, lineDone = false;
  let autoScheduled = false, activity = false, openToken = 0;
  let observer, frameEvents, positionFrame, lines = [], targetIndex = 0, focusGroup = [], playbackClock;
  let textElapsed = 0, textStarted = 0, textDuration = 0, finishLine, usingText = false, ended = false;
  let serverDismissed = null, serverDismissedFor = '';
  const $ = name => shell?.querySelector('[data-guide="' + name + '"]');
  const key = () => 'mm.guide.v1.' + profileId;
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const sprite = (role, expression) => BASE + 'guide-' + role + '-' + expression + '.webp';

  function script(name) {
    if (!scripts.has(name)) scripts.set(name, new Promise((resolve, reject) => {
      const node = document.createElement('script'); node.src = BASE + name;
      node.onload = resolve; node.onerror = () => { scripts.delete(name); node.remove(); reject(Error('导览资源读取失败')); };
      document.head.append(node);
    }));
    return scripts.get(name);
  }
  async function ready() {
    if (!readyPromise) readyPromise = Promise.all([
      script('guide-state.js').then(()=>script('guide-service.js?v=2')), script('guide-scenes.js'),
      fetch(BASE + 'guide-content.json').then(r => { if (!r.ok) throw Error('讲解内容读取失败'); return r.json(); }),
      fetch(BASE + 'guide-audio.json').then(r => r.ok ? r.json() : {clips:{}}).catch(() => ({clips:{}}))
    ]).then(result => { content = result[2]; audioManifest = result[3]; }).catch(error => { readyPromise = null; throw error; });
    await readyPromise;
    if (!document.getElementById('guide-style')) {
      const link = document.createElement('link'); link.id = 'guide-style'; link.rel = 'stylesheet'; link.href = BASE + 'guide.css'; document.head.append(link);
    }
    const current = localStorage.getItem('mm_profile_id') || 'guest';
    if (current !== profileId || !state) {
      if(shell&&profileId&&current!==profileId)close();
      profileId = current;
      let saved;
      try { saved = JSON.parse(localStorage.getItem(key())); } catch { /* Invalid checkpoints start clean. */ }
      const storageKey=key();
      service = GuideService.create(content, saved, {
        legacyCompleted:localStorage.getItem('ob_completed')==='1' && new URLSearchParams(location.search).get('tour')!=='1',
        persist:next=>{try{localStorage.setItem(storageKey,JSON.stringify(next));}catch{}updateEntry();}
      });
      state = service.state;
      updateEntry();
    }
  }
  function save() { service?.save(); }
  function updateEntry() { const entry=document.getElementById('guide-menu');if(entry)entry.textContent=service?.isCompleted()?'重播引导':'使用引导'; }
  // 「不再提醒」除了浏览器本地记录，还写到服务端本地文件：换端口、清缓存、重启服务都不会再自动弹出。
  async function guideDismissed() {
    if (!profileId || profileId === 'guest') return false;
    if (serverDismissed !== null && serverDismissedFor === profileId) return serverDismissed;
    try {
      const response = await fetch('/api/guide/settings?pid=' + encodeURIComponent(profileId));
      const data = await response.json();
      serverDismissed = !!data.dismissed;
    } catch { serverDismissed = false; }
    serverDismissedFor = profileId;
    return serverDismissed;
  }
  async function rememberDismissed() {
    serverDismissed = true; serverDismissedFor = profileId;
    if (!profileId || profileId === 'guest') return;
    try {
      await fetch('/api/guide/settings', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({pid: profileId, dismissed: true})});
    } catch { /* 浏览器存储已记录；服务端下次仍可补写 */ }
  }
  function checkpoint() {
    if (mode === 'step' && block && step) {
      service.checkpoint(block.id, step.id, Math.min(lineIndex, step.lines.length - 1), lineDone);
    }
  }
  function stopVoice() {
    token++; clearTimeout(timer); clearInterval(playbackClock); timer = null; playbackClock = null;
    if (voice) { voice.pause(); voice.removeAttribute('src'); voice.load(); voice = null; }
    $('alpha')?.classList.remove('speaking'); $('xiaobei')?.classList.remove('speaking');
  }
  function mount() {
    if (shell) return;
    originFocus = document.activeElement; originScroll = {x:scrollX, y:scrollY};
    shell = document.createElement('dialog'); shell.className = 'guide-shell';
    shell.setAttribute('aria-label', '阿尔法与小北的使用引导');
    shell.innerHTML = `
      <header class="guide-top">
        <div class="guide-heading"><h1 class="guide-title" data-guide="title">使用引导</h1><div class="guide-progress"><span class="guide-progress-track"><i data-guide="bar"></i></span><span data-guide="progress"></span></div></div>
        <label class="guide-voice-label"><input type="checkbox" data-guide="sound">语音</label>
        <button class="guide-icon" data-guide="exit" title="先到这里，保存导览进度" aria-label="退出导览">&times;</button>
        <div class="guide-xiaobei" data-guide="xiaobei"><img alt="小北" src="${sprite('xiaobei','cheerful')}"></div>
      </header>
      <section class="guide-stage" data-guide="stage">
        <iframe class="guide-frame" data-guide="frame" title="应用示例界面" sandbox="allow-scripts allow-same-origin"></iframe>
        <div class="guide-shade" data-guide="shade-top"></div><div class="guide-shade" data-guide="shade-bottom"></div><div class="guide-shade" data-guide="shade-left"></div><div class="guide-shade" data-guide="shade-right"></div>
        <div class="guide-ring" data-guide="ring"></div>
        <div class="guide-choice guide-hidden" data-guide="choice"></div>
      </section>
      <footer class="guide-caption">
        <div class="guide-speaker-row"><span class="guide-speaker" data-guide="speaker">阿尔法</span><span class="guide-line-count" data-guide="line-count"></span><span class="guide-audio-status" data-guide="audio-status"></span></div>
        <p class="guide-line" data-guide="line" aria-live="polite"></p>
        <div class="guide-playback"><button class="guide-icon" data-guide="replay" title="重播这一句" aria-label="重播这一句">&#8635;</button><button class="guide-icon" data-guide="pause" title="暂停讲解" aria-label="暂停讲解">&#10074;&#10074;</button></div>
        <div class="guide-actions"><button data-guide="prev">上一步</button><button data-guide="skip-step">跳过本步</button><button data-guide="skip-all" title="跳过全部，不再自动提示，仍可手动打开">不再提示</button><button class="guide-primary" data-guide="line-next">下一句</button></div>
        <p class="guide-help" data-guide="help"></p>
      </footer><div class="guide-alpha" data-guide="alpha"><img alt="阿尔法" src="${sprite('alpha','calm')}"></div>`;
    document.body.append(shell); frame = $('frame');
    $('sound').checked = !state.muted;
    $('sound').onchange = () => {
      service.setMuted(!$('sound').checked);
      if (!lines.length) return;
      if (voice) voice.muted = state.muted;
      if (!paused && !lineDone) $('audio-status').textContent = state.muted ? '已静音' : usingText ? '字幕' : '播放中';
    };
    $('exit').onclick = close; $('pause').onclick = togglePause;
    $('replay').onclick = () => playLine(lineIndex);
    $('line-next').onclick = advanceCue;
    $('skip-step').onclick = skipStep;
    $('skip-all').onclick = ()=>{checkpoint();service.skipAll();close(false);void rememberDismissed();};
    $('prev').onclick = () => { if (mode === 'step' && step.id > block.first) showStep(step.id - 1); };
    shell.addEventListener('cancel', e => { e.preventDefault(); close(); });
    shell.addEventListener('keydown', e => {
      if (e.key === 'ArrowRight' && !e.target.matches('input,textarea,select')) { e.preventDefault(); advanceCue(); }
      if (e.key === 'ArrowLeft' && !e.target.matches('input,textarea,select')) { e.preventDefault(); $('prev').click(); }
    });
    shell.showModal(); service.presented(); $('exit').focus();
    window.addEventListener('resize', schedulePosition);
    observer = new ResizeObserver(schedulePosition); observer.observe($('stage'));
  }
  function close(dismiss = true) {
    checkpoint(); stopVoice(); sceneToken++; openToken++;
    if(dismiss!==false)service?.close();
    if (!shell) return;
    frameEvents?.abort(); frameEvents = null; observer?.disconnect(); observer = null;
    cancelAnimationFrame(positionFrame); window.removeEventListener('resize', schedulePosition);
    shell.close(); shell.remove(); shell = null; frame = null; lines = []; mode = '';
    originFocus?.isConnected && originFocus.focus({preventScroll:true});
    if (originScroll) window.scrollTo(originScroll.x, originScroll.y);
    const done = callback; callback = null; done?.();
  }
  function choice(title, description, buttons, list = '') {
    checkpoint();mount(); stopVoice(); sceneToken++; mode = 'choice'; lines = []; hideSpotlight();
    $('choice').classList.remove('guide-hidden');
    $('choice').innerHTML = `<div class="guide-choice-inner"><div class="guide-welcome-characters"><img alt="阿尔法" src="${sprite('alpha','calm')}"></div><h2>${esc(title)}</h2><p>${esc(description)}</p>${list}<div class="guide-choice-actions"></div></div>`;
    const actions = $('choice').querySelector('.guide-choice-actions');
    for (const [label, fn, primary] of buttons) {
      const b = document.createElement('button'); b.textContent = label; b.className = primary ? 'guide-primary' : ''; b.onclick = fn; actions.append(b);
    }
    $('speaker').textContent = '小北'; $('line').textContent = '我是小北，在后台接应你们。阿尔法负责带路，我来补充！';
    $('line-count').textContent = ''; $('audio-status').textContent = ''; $('help').textContent = '';
    $('progress').textContent = '六个板块 · 可分次完成'; $('bar').style.width = '0%';
    $('prev').disabled = true;
    for (const name of ['pause','replay','line-next','skip-step']) $(name).disabled = true;
  }
  async function openPicker() {
    try {
      await ready();
      choice('想从哪里开始？', '小北：上次没讲完的地方，我还记着呢。', [['先到这里', close]], '<div class="guide-blocks"></div>');
      const labels = {unseen:'未开始',pending:'待讲解',active:'继续',completed:'重新听'};
      const list = $('choice').querySelector('.guide-blocks');
      for (const b of content.blocks) {
        const button = document.createElement('button'); button.className = 'guide-block';
        button.innerHTML = `<span>${esc(b.title)}</span><small>${labels[state.blocks[b.id].status]}</small>`;
        button.onclick = () => startBlock(b.id); list.append(button);
      }
    } catch (error) { showError(error); }
  }
  async function openGuide() {
    try {
      await ready();
      if(service.allBlocksComplete()&&!service.isCompleted()){finishGuide();return;}
      return startBlock();
    } catch(error) { showError(error); }
  }
  async function startBlock(id, replay = false, manual = true) {
    const ticket=++openToken;
    try {
      await ready();if(ticket!==openToken)return;
      const result=service.open({manual,blockId:id,replayConfirmed:replay});
      if(result.action==='none')return;
      if(result.action==='confirm_replay'){
        choice('是否重新观看？','完成记录会保留。', [['重新观看',()=>startBlock(result.blockId,true),true],['取消',close],['选择板块',openPicker]]);return;
      }
      mount();
      block = content.blocks.find(b => b.id === result.blockId);
      const progress = result.progress;
      if (progress.lineDone) {
        const current = content.steps.find(s => s.id === progress.step);
        if (progress.line + 1 < current.lines.length) progress.line++;
        else if (progress.step < block.last) { progress.step++; progress.line = 0; }
        else { step = current; finishBlock(); return; }
        progress.lineDone = false; save();
      }
      await showStep(progress.step, progress.line);
    } catch (error) { showError(error); }
  }
  async function start(done) { callback = typeof done === 'function' ? done : null; return openGuide(); }
  function showError(error) {
    if (!state) {
      const dialog=document.createElement('dialog'),text=document.createElement('p'),retry=document.createElement('button'),cancel=document.createElement('button');
      text.textContent='引导暂时无法加载：'+error.message;retry.textContent='重试';cancel.textContent='关闭';
      retry.onclick=()=>{dialog.close();dialog.remove();openGuide();};cancel.onclick=()=>{dialog.close();dialog.remove();};
      dialog.addEventListener('close',()=>dialog.remove());dialog.append(text,retry,cancel);document.body.append(dialog);dialog.showModal();return;
    }
    const buttons=[['重试', () => block ? startBlock(block.id) : openPicker(), true]];
    if(block&&step)buttons.push(['跳过本步',skipStep]);
    buttons.push(['先到这里',close]);
    choice('这一页暂时没有准备好', error.message || '可以重试，或稍后继续。',buttons);
  }
  async function previewDocument(page, id) {
    if (!sources.has(page)) {
      const res = await fetch(BASE + page + '.html'); if (!res.ok) throw Error('示例页面读取失败'); sources.set(page, await res.text());
    }
    const doc = new DOMParser().parseFromString(sources.get(page), 'text/html');
    // Strip business scripts before parsing the preview into a live browsing context.
    doc.querySelectorAll('script,iframe,object,embed,base,meta[http-equiv]').forEach(n => n.remove());
    for (const node of doc.querySelectorAll('*')) for (const attr of [...node.attributes]) {
      if (/^on/i.test(attr.name) || attr.name === 'autofocus') node.removeAttribute(attr.name);
    }
    doc.querySelectorAll('link[href],img[src]').forEach(n => {
      const attr = n.tagName === 'LINK' ? 'href' : 'src', url = new URL(n.getAttribute(attr), location.origin);
      if (url.origin !== location.origin || !url.pathname.startsWith('/static/')) n.remove(); else n.setAttribute(attr, url.href);
    });
    const base = doc.createElement('base'); base.href = location.origin + '/'; doc.head.prepend(base);
    const policy = doc.createElement('meta'); policy.httpEquiv = 'Content-Security-Policy';
    policy.content = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'none'; form-action 'none'; base-uri 'self'"; doc.head.prepend(policy);
    doc.documentElement.dataset.guidePreview = '1'; doc.documentElement.dataset.guideStep = String(id);
    doc.documentElement.dataset.guidePage = page === 'workspace' ? 'free' : page;
    const css = doc.createElement('link'); css.rel = 'stylesheet'; css.href = BASE + 'guide-preview.css'; doc.head.append(css);
    const allow = ['vendor/npm/marked@12.0.2/marked.min.js','render.js','vendor/npm/codemirror@5.65.16/lib/codemirror.min.js','vendor/npm/codemirror@5.65.16/mode/python/python.min.js','solve-flow.js','stepflow.js','learn.js','assessment.js','ui.js','guide-preview.js'];
    for (const path of allow) { const s = doc.createElement('script'); s.src = BASE + path; s.defer = true; doc.head.append(s); }
    return '<!DOCTYPE html>\n' + doc.documentElement.outerHTML;
  }
  async function showStep(id, resumeLine = 0) {
    stopVoice(); mode = 'step'; step = content.steps.find(s => s.id === id); if (!step) return;
    const ticket = ++sceneToken;
    lines = step.lines; lineIndex = resumeLine; lineDone = false; ended = false; targetIndex = 0; activity = false; checkpoint(); mount(); $('choice').classList.add('guide-hidden');
    $('title').textContent = block.title; $('progress').textContent = `${id - block.first + 1} / ${block.last - block.first + 1} · 示例`;
    $('bar').style.width = (id - block.first + 1) / (block.last - block.first + 1) * 100 + '%';
    $('alpha').querySelector('img').src = sprite('alpha', step.alpha); $('xiaobei').querySelector('img').src = sprite('xiaobei', step.xiaobei);
    $('speaker').textContent = '阿尔法'; $('line').textContent = '正在准备这一页…'; $('audio-status').textContent = ''; $('help').textContent = '';
    $('prev').disabled = true;
    for (const name of ['pause','replay','line-next','skip-step']) $(name).disabled = true;
    hideSpotlight(); frameEvents?.abort(); frameEvents = new AbortController();
    try {
      const source = await previewDocument(GuideScenes[id].page, id);
      if (ticket !== sceneToken || !shell) return;
      await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(Error('示例界面加载超时')), 12000);
        frame.onload = () => {
          clearTimeout(timeout); if (ticket !== sceneToken) return resolve();
          const preview = frame.contentWindow.GuidePreview;
          if (!preview?.ready) reject(Error(preview?.error || '示例界面未能加载')); else resolve();
        };
        frame.srcdoc = source;
      });
      if (ticket !== sceneToken || !shell) return;
      const doc = frame.contentDocument;
      doc.addEventListener('scroll', schedulePosition, {capture:true, signal:frameEvents.signal});
      doc.addEventListener('guide-action', () => { activity = true; $('help').textContent = '已打开，可以继续查看。'; position(true); }, {signal:frameEvents.signal});
      doc.addEventListener('keydown', e => { if (e.key === 'Escape') { e.preventDefault(); close(); } }, {signal:frameEvents.signal});
      const actionLabels = {verification:'点亮处：计算与验证',code:'点亮处：代码编辑',outcome:'点亮处：本步成果',reader:'点亮处：打开知识点'};
      $('help').textContent = actionLabels[GuideScenes[id].action] || '';
      position(true);
      if (ticket === sceneToken && shell) {
        $('prev').disabled = id <= block.first;
        for (const name of ['pause','replay','line-next','skip-step']) $(name).disabled = false;
        playLine(resumeLine);
      }
    } catch (error) { if (ticket === sceneToken && shell) showError(error); }
  }
  function advanceCue() {
    if (!shell || $('line-next').disabled) return;
    if (lineIndex + 1 < lines.length) { playLine(lineIndex + 1); return; }
    if (mode === 'step') advanceStep();
  }
  function advanceStep() {
    if (!shell || mode !== 'step') return;
    if (step.id < block.last) showStep(step.id + 1); else finishBlock();
  }
  function skipStep() {
    if(!block||!step)return;
    const next=service.skipStep(block.id,step.id);
    if(next!=null)showStep(next);
    else {
      stopVoice();mode='boundary';
      choice('本步已跳过','是否结束这一部分？', [['完成本部分',finishBlock,true],['先到这里',close]]);
    }
  }
  function finishGuide() {
    choice('这一圈，走完了。','之后仍可从使用引导入口重新观看。', [
      ['完成引导',()=>{service.complete();close(false);},true],['先到这里',close]
    ]);
  }
  function finishBlock() {
    stopVoice(); const next = service.completeBlock(block.id);
    mode='boundary';
    if(!next && service.allBlocksComplete()&&!service.isCompleted()){finishGuide();return;}
    if (!next || step.id === 26) {
      const done = service.isCompleted();
      choice(done ? '这一圈，走完了。' : '这一部分，就到这里。', done ? '小北：接下来，选一件你想做的事吧！' : '小北：还没看的部分我记着呢，下次打开对应界面，我们再接着说。', [
        ['开始我的项目', () => { close(); location.href='/solve'; }, true], ['先去学习', () => { close(); location.href='/learn'; }], ['回到原界面', close]
      ]);
      $('progress').textContent = Object.values(state.blocks).filter(b=>b.status==='completed').length + ' / 6 板块已完成'; return;
    }
    choice('接下来：' + next.title, '这一部分已经介绍完了。', [
      ['继续了解' + next.title, () => startBlock(next.id,service.replaying), true], ['先到这里', close]
    ]);
    mode = 'boundary'; lines = content.transitions[next.id]; $('progress').textContent = '是否继续，由你决定';
    for (const name of ['pause','replay','line-next']) $(name).disabled = false;
    playLine(0);
  }
  function playLine(index) {
    if (!shell || index >= lines.length) return;
    stopVoice(); paused = false; lineDone = false; ended = false; usingText = false; lineIndex = Math.max(0,index);
    const item = lines[lineIndex], ticket = token;
    checkpoint(); $('speaker').textContent = item.role === 'alpha' ? '阿尔法' : '小北'; $('line').textContent = item.text; $('line').scrollTop = 0;
    $('line-count').textContent = `${lineIndex+1}/${lines.length}`; $('line-next').disabled = mode !== 'step' && lineIndex >= lines.length - 1; updatePause();
    $(item.role)?.classList.add('speaking');
    if(mode==='step'){
      if (GuideScenes[step.id].action && !activity && lineIndex > 0) {
        frame.contentWindow.GuidePreview.activate(GuideScenes[step.id].action);
        $('help').textContent = '已展开示例，可以继续查看。';
      }
      const alphaIndex=lines.slice(0,lineIndex+1).filter(l=>l.role==='alpha').length-1;
      const groups=GuideScenes[step.id].focuses;
      focusGroup=item.role==='alpha'?(groups[alphaIndex]||[0]):[groups.at(-1).at(-1)];
      targetIndex=focusGroup[0];position(true);
      const alphaState=step.id===8?(alphaIndex===0?'warning':'analysis'):step.id===9?(alphaIndex===0?'calm':'approval'):step.id===10?(alphaIndex===0?'warning':'calm'):step.id===17?(alphaIndex===0?'analysis':'approval'):step.id===24?(alphaIndex===2&&item.role==='alpha'?'warning':'calm'):step.alpha;
      $('alpha').querySelector('img').src=sprite('alpha',alphaState);
    }
    const end = () => {
      if (ticket !== token || !shell || ended) return;
      ended = true; lineDone = true; checkpoint(); clearInterval(playbackClock);
      if(mode==='step'&&focusGroup.length){targetIndex=focusGroup.at(-1);position(true);}
      $(item.role)?.classList.remove('speaking');
      if (lineIndex + 1 < lines.length) timer = setTimeout(() => { if (ticket === token) playLine(lineIndex+1); }, 250);
      else { lineDone = true; $('audio-status').textContent = '本段结束'; updatePause(); }
    };
    finishLine=end;
    const textPlayback = () => {
      if(usingText||ticket!==token)return;
      usingText=true;voice?.pause();textElapsed=0;textStarted=performance.now();
      textDuration=Math.max(2200,Math.min(14000,item.text.length*180));
      $('audio-status').textContent = state.muted ? '已静音' : '字幕'; timer = setTimeout(end, textDuration);
    };
    playbackClock=setInterval(()=>{
      if(paused||ended||mode!=='step'||focusGroup.length<2)return;
      const fraction=usingText?(textElapsed+performance.now()-textStarted)/textDuration:voice?.currentTime/voice?.duration;
      if(!Number.isFinite(fraction))return;
      const next=focusGroup[Math.min(focusGroup.length-1,Math.floor(fraction*focusGroup.length))];
      if(next!==targetIndex){targetIndex=next;position(true);}
    },120);
    const clip = audioManifest.clips?.[item.id];
    if (!clip || clip.stale) { textPlayback(); return; }
    $('audio-status').textContent = '播放中'; voice = new Audio(BASE + (typeof clip === 'string' ? clip : clip.file));
    voice.muted = state.muted;
    if (state.muted) $('audio-status').textContent = '已静音';
    voice.onended = end; voice.onerror = () => { if (ticket === token) textPlayback(); };
    voice.play().catch(error => {
      if (ticket !== token) return;
      if (error.name === 'NotAllowedError' && !state.muted) { paused = true; $('audio-status').textContent = '点击继续播放'; updatePause(); } else textPlayback();
    });
  }
  function updatePause() {
    if (!shell) return;
    $('pause').disabled = lineDone && lineIndex === lines.length - 1;
    $('pause').innerHTML = paused || lineDone ? '&#9654;' : '&#10074;&#10074;';
    const label = lineDone && lineIndex === lines.length - 1 ? '本句已结束' : paused ? '继续播放' : '暂停讲解'; $('pause').title = label; $('pause').setAttribute('aria-label',label);
  }
  function togglePause() {
    if (!lines.length) return;
    if(lineDone && lineIndex === lines.length - 1)return;
    if(paused){
      paused=false;
      if(ended){timer=setTimeout(()=>playLine(lineIndex+1),250);}
      else if(usingText){textStarted=performance.now();timer=setTimeout(finishLine,Math.max(0,textDuration-textElapsed));}
      else if(voice){const ticket=token;voice.play().catch(()=>{if(ticket===token){if(state.muted){voice.onerror?.();return;}paused=true;$('audio-status').textContent='点击继续播放';updatePause();}});}
      $(lines[lineIndex].role)?.classList.add('speaking');$('audio-status').textContent=state.muted?'已静音':usingText?'字幕':'播放中';updatePause();return;
    }
    paused = true; clearTimeout(timer); if(usingText)textElapsed+=performance.now()-textStarted;
    voice?.pause(); $('audio-status').textContent = '已暂停'; updatePause();
    $('alpha').classList.remove('speaking'); $('xiaobei').classList.remove('speaking');
  }
  function hideSpotlight() { shell?.classList.remove('guide-docked');for (const name of ['alpha','ring','shade-top','shade-bottom','shade-left','shade-right']) $(name)?.classList.add('guide-hidden'); }
  function schedulePosition() { cancelAnimationFrame(positionFrame); positionFrame = requestAnimationFrame(() => position(false)); }
  function position(scroll) {
    if (!shell || mode !== 'step' || !frame?.contentWindow.GuidePreview?.ready) return;
    const scene = GuideScenes[step.id], doc = frame.contentDocument, targets = scene.targets;
    let currentTarget=targetIndex;
    if (scene.action && !activity) currentTarget = 0;
    if (scene.action && activity) currentTarget = Math.max(1,currentTarget);
    frame.contentWindow.GuidePreview.focus(targets[currentTarget]);
    let node = doc.querySelector(targets[currentTarget]);
    if (!node?.getClientRects().length) node = targets.map(s=>doc.querySelector(s)).find(n=>n?.getClientRects().length);
    if (!node) { showError(Error('讲解区域暂未展开，可以重试或跳过本步。')); return; }
    doc.querySelectorAll('[data-tour]').forEach(n=>n.removeAttribute('data-tour'));
    node.setAttribute('data-tour', String(step.id));
    if (scroll) node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    const rect = node.getBoundingClientRect(), stage = $('stage'), w = stage.clientWidth, h = stage.clientHeight;
    const left=Math.max(2,rect.left-10),top=Math.max(2,rect.top-10),right=Math.min(w-2,rect.right+10),bottom=Math.min(h-2,rect.bottom+10);
    const width=Math.max(0,right-left),height=Math.max(0,bottom-top); if (!width || !height) { hideSpotlight(); return; }
    for (const name of ['alpha','ring','shade-top','shade-bottom','shade-left','shade-right']) $(name).classList.remove('guide-hidden');
    function box(name,x,y,bw,bh) { Object.assign($(name).style,{left:x+'px',top:y+'px',width:Math.max(0,bw)+'px',height:Math.max(0,bh)+'px'}); }
    box('ring',left,top,width,height); box('shade-top',0,0,w,top); box('shade-bottom',0,bottom,w,h-bottom); box('shade-left',0,top,left,height); box('shade-right',right,top,w-right,height);
    const alpha=$('alpha'),aw=alpha.offsetWidth,ah=alpha.offsetHeight;
    const candidates=[[right+16,top],[left-aw-16,top],[left,bottom+16],[left,top-ah-16]];
    const spot=candidates.find(([x,y])=>x>=8&&x+aw<=w-8&&y>=8&&y+ah<=h-8);
    alpha.classList.remove('guide-hidden');
    shell.classList.toggle('guide-docked',!spot);
    const caption=shell.querySelector('.guide-caption'),mobile=w<768;
    Object.assign(alpha.style,{left:(spot?stage.offsetLeft+spot[0]:mobile?14:24)+'px',top:(spot?stage.offsetTop+spot[1]:caption.offsetTop+(mobile?24:(caption.offsetHeight-ah)/2))+'px'});
  }
  function busy() {
    if (document.getElementById('profile-load-error')) return true;
    if (window.IDE?.isRunning?.() || window.SolveFlow?.isBusy?.() || window.SolveFlow?.isDirty?.()) return true;
    if (document.querySelector('dialog[open]')) return true;
    if ([...document.querySelectorAll('.ks-overlay,.role-modal-overlay,.wait-hub:not(.is-finished),.concept-reader,.settings-overlay')].some(n=>!n.hidden&&n.getClientRects().length && getComputedStyle(n).visibility!=='hidden' && getComputedStyle(n).opacity!=='0')) return true;
    const folder=document.querySelector('.fm-overlay:not([hidden])');if(folder?.contains(document.activeElement)&&document.activeElement.matches('input,textarea,[contenteditable="true"]'))return true;
    if ([...document.querySelectorAll('textarea:not([readonly]):not(:disabled)')].some(n=>n.getClientRects().length&&n.value.trim())) return true;
    return document.activeElement?.matches('input,textarea,[contenteditable="true"]');
  }
  async function autoStart(requested) {
    if (shell || autoScheduled) return;
    // Reload and history restoration must not reopen an interrupted tour.
    // Explicit starts from the guide menu remain available.
    const navigation = performance.getEntriesByType('navigation')[0]?.type;
    if (navigation === 'reload' || navigation === 'back_forward') return;
    autoScheduled = true;
    try {
      await ready();
      if (profileId==='guest' || !service.canAutoOpen()) return;
      if (await guideDismissed()) return;
      const candidate=requested?content.blocks.find(b=>b.id===requested):GuideState.match(content.blocks,location.pathname);
      if (!candidate || state.blocks[candidate.id].status==='completed') return;
      if (busy() || document.visibilityState!=='visible') { setTimeout(()=>autoStart(requested),1500); return; }
      if (candidate.id==='home' && state.blocks.home.status==='unseen') {
        choice('先认识一下这里', '阿尔法带路，小北在后台接应。先用半分钟看看主页，其他部分可以以后再逛。', [
          ['先认识主页',()=>startBlock('home'),true],['先到这里',close]
        ]);
      } else await startBlock(candidate.id,false,false);
    } catch (error) { console.warn('Guide:',error.message); } finally { autoScheduled=false; }
  }
  async function prologue() {
    await ready(); if (localStorage.getItem('mm_profile_id') || document.getElementById('guide-prologue')) return;
    const anchor=document.querySelector('.landing-brand');if(!anchor)return;
    const welcome=document.createElement('section');welcome.id='guide-prologue';welcome.className='guide-prologue';
    welcome.innerHTML=`<div class="guide-prologue-bei-fixed guide-xiaobei"><img alt="小北" src="${sprite('xiaobei','cheerful')}"></div>`+['alpha','xiaobei'].map(role=>`<div class="guide-prologue-row">${role==='alpha'?`<img alt="阿尔法" src="${sprite(role,'calm')}">`:'<span class="guide-prologue-spacer"></span>'}<p><strong>${role==='alpha'?'阿尔法':'小北'}</strong>${content.prologue.filter(l=>l.role===role).map(l=>esc(l.text)).join('')}</p></div>`).join('')+'<button class="ui-btn guide-prologue-listen" type="button">听听我们的介绍</button>';anchor.after(welcome);
    const listen=welcome.querySelector('button'),portrait=welcome.querySelector('.guide-xiaobei');document.body.append(portrait);let introVoice,introIndex=0,introPlaying=false;
    function stopIntro(){introPlaying=false;introVoice?.pause();introVoice=null;portrait.classList.remove('speaking');listen.textContent='听听我们的介绍';}
    function introLine(){
      if(!introPlaying||introIndex>=content.prologue.length){stopIntro();return;}
      const item=content.prologue[introIndex],clip=audioManifest.clips?.[item.id];
      if(!clip || clip.stale){stopIntro();return;}
      portrait.classList.toggle('speaking',item.role==='xiaobei');
      introVoice=new Audio(BASE+clip.file);introVoice.onended=()=>{introIndex++;setTimeout(introLine,250);};introVoice.onerror=stopIntro;introVoice.play().catch(stopIntro);
    }
    const introAvailable=content.prologue.every(item=>audioManifest.clips?.[item.id]&&!audioManifest.clips[item.id].stale);
    listen.disabled=!introAvailable;
    if(!introAvailable)listen.textContent='配音待更新';
    listen.onclick=()=>{if(introPlaying){stopIntro();return;}introPlaying=true;introIndex=0;listen.textContent='停止介绍';introLine();};
    window.addEventListener('pagehide',stopIntro,{once:true});
  }
  function bootstrap() {
    if (location.pathname==='/') {prologue().catch(()=>{});return;}
    const menu=document.querySelector('.ui-menu-items');
    if (menu && !document.getElementById('guide-menu')) {
      const entry=document.createElement('button');entry.id='guide-menu';entry.textContent='使用引导';entry.onclick=()=>{entry.closest('details').open=false;openGuide();};menu.append(entry);
      const chapters=document.createElement('button');chapters.textContent='选择引导板块';chapters.onclick=()=>{chapters.closest('details').open=false;openPicker();};menu.append(chapters);
    }
    ready().catch(()=>{});setTimeout(()=>autoStart(),600);
  }
  window.OnboardingTour={start,startBlock,open:openGuide,openPicker,close,resume:()=>openGuide(),settingsOpened:()=>{setTimeout(()=>autoStart('tools'),350);},
    reset:async()=>{await ready();close();service=GuideService.create(content,null,{persist:next=>{try{localStorage.setItem(key(),JSON.stringify(next));}catch{}updateEntry();}});state=service.state;save();},isCompleted:()=>!!service&&service.isCompleted(),
    get progress(){return state?JSON.parse(JSON.stringify(state)):null;}};
  window.addEventListener('pagehide',()=>{checkpoint();stopVoice();});
  window.addEventListener('beforeunload',checkpoint);
  window.addEventListener('storage',event=>{if(event.key==='mm_profile_id' && shell)close();});
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',bootstrap,{once:true});else bootstrap();
})();
