/* Page-independent chat: the server owns execution; pages only read its journal. */
(function () {
  'use strict';
  if (window.ChatTasks) return;
  const nativeFetch = window.fetch.bind(window);
  const key = 'mm_chat_tasks_v1', records = new Map(), subscriptions = new Map(), submissions = new Map();
  const terminal = status => ['completed', 'failed', 'cancelled', 'interrupted'].includes(status);
  const uuid = () => window.crypto?.randomUUID?.() || 'msg-' + Date.now() + '-' + Math.random().toString(36).slice(2);
  const duration = ms => { const seconds=Math.max(0,Math.floor(ms/1000));return Math.floor(seconds/60)+':'+String(seconds%60).padStart(2,'0'); };
  // A heartbeat checks this application's reachability, never model availability.
  // Read-only checks must not retry a generation request or cancel its owner.
  function watch(target, {label='AI 请求', task=null}={}) {
    if(!target)return {setTask(){},finish(){},dispose(){}};
    const prior=target.nextElementSibling;
    if(prior?.classList.contains('request-vitals'))prior.remove();
    const node=document.createElement('div');node.className='request-vitals';
    const clock=document.createElement('span'),state=document.createElement('span'),age=document.createElement('span');
    state.setAttribute('role','status');state.setAttribute('aria-live','polite');
    node.append(clock,state,age);target.after(node);
    let started=task?.created_at?task.created_at*1000:Date.now(),ended=null,lastBeat=0,nextProbe=0,control=null,timeout=null,tick=null,closed=false;
    let stateText='正在检查服务连接';
    const draw=()=>{
      clock.textContent=label+' · '+(ended===null?'已用时 ':'总用时 ')+duration((ended??Date.now())-started);
      if(state.textContent!==stateText)state.textContent=stateText;
      age.textContent=ended===null&&lastBeat?'上次心跳 '+Math.max(0,Math.floor((Date.now()-lastBeat)/1000))+' 秒前':'';
    };
    const stop=()=>{closed=true;clearTimeout(tick);clearTimeout(timeout);control?.abort();window.removeEventListener('pagehide',dispose);};
    const finish=(text='已结束',at=Date.now())=>{if(closed)return;ended=Math.max(started,at);stateText=text;stop();draw();};
    function dispose(){stop();node.remove();}
    const probe=async()=>{
      control=new AbortController();timeout=setTimeout(()=>control.abort(),4000);
      try{
        const result=await json(task?'/api/chat/tasks/'+encodeURIComponent(task.id):'/api/health',{signal:control.signal,cache:'no-store'});
        if(closed)return;
        lastBeat=Date.now();node.dataset.connection='online';
        if(task&&terminal(result.status)){
          const labels={completed:'已完成',failed:'生成失败',interrupted:'生成已中断',cancelled:'已停止'};
          finish(labels[result.status],Number(result.updated_at||Date.now()/1000)*1000);return;
        }
        stateText=task?(result.status==='queued'?'服务心跳正常 · 排队中':'服务心跳正常 · 后台任务进行中'):'服务心跳正常 · 等待模型结果';
      }catch(error){if(!closed){node.dataset.connection='offline';stateText=task&&[404,410].includes(error.status)?'任务记录暂不可用，请检查存档':'服务心跳未响应 · 正在重新检测';}}
      finally{clearTimeout(timeout);control=null;if(!closed)draw();}
    };
    const update=()=>{if(closed)return;draw();if(!control&&Date.now()>=nextProbe){nextProbe=Date.now()+10000;void probe();}tick=setTimeout(update,1000);};
    window.addEventListener('pagehide',dispose,{once:true});update();
    return {setTask(value){task=value;if(task?.created_at)started=task.created_at*1000;nextProbe=0;draw();},finish,dispose};
  }
  let timer, clockTimer, refreshing, center, summary, list, stopped = false, monitorEpoch = 0, lastTaskBeat = 0;
  function paintTimes() {
    if(list)for(const element of list.querySelectorAll('[data-task-time]')){
      const task=records.get(element.dataset.taskTime);if(!task?.created_at)continue;
      const done=terminal(task.status),end=done?Number(task.updated_at||task.created_at)*1000:Date.now();
      const connection=done?'':lastTaskBeat&&Date.now()-lastTaskBeat<20000?' · 服务心跳正常':' · 服务心跳待确认';
      element.textContent=(done?'总用时 ':'已用时 ')+duration(end-task.created_at*1000)+connection;
    }
  }
  function clockTick(){clearTimeout(clockTimer);if(stopped)return;paintTimes();clockTimer=setTimeout(clockTick,1000);}
  function readCache() {
    try { const value = JSON.parse(localStorage.getItem(key) || '[]'); return Array.isArray(value) ? value : []; } catch { return []; }
  }
  function persist() {
    // Only task references and UI metadata: full messages/events remain on the server.
    const external = new Map(readCache().map(item => [item.id, item]));
    const items = [...records.values()].slice(-100).map(item => ({
      id: item.id, client_message_id: item.client_message_id, conversation_id: item.conversation_id,
      kind: item.kind, status: item.status, url: item.url, title: item.title,
      acknowledged: item.acknowledged || external.get(item.id)?.acknowledged || false,
      updated_at: item.updated_at, created_at: item.created_at,
      invalidated: item.invalidated || external.get(item.id)?.invalidated || false,
      invalidated_reason: item.invalidated_reason, invalidated_at: item.invalidated_at,
    }));
    try { const serialized = JSON.stringify(items); if (localStorage.getItem(key) !== serialized) localStorage.setItem(key, serialized); } catch { /* Storage denied/full: server recovery still works. */ }
  }
  for (const item of readCache()) if (item?.id) records.set(item.id, item);
  function remember(task, metadata = {}, flush = true) {
    const old = records.get(task.id) || records.get(task.client_message_id) || {};
    const item = {...old, ...task, url: metadata.url || old.url || task.url,
      title: metadata.title || old.title || task.title, acknowledged: old.acknowledged || false};
    if (task.id !== task.client_message_id) records.delete(task.client_message_id);
    const reference = {...item};
    delete reference.request; delete reference.events;
    records.set(task.id, reference);
    // A long-lived workspace must not accumulate replay payloads or unlimited task history.
    if (records.size > 120) {
      for (const [id, record] of records) {
        if (records.size <= 120) break;
        if (terminal(record.status)) records.delete(id);
      }
    }
    if (flush) { persist(); paint(); }
    return item;
  }
  function taskURL(task) {
    let target;
    try { target = new URL(task.url || (task.kind === 'solve' ? '/solve' : '/workspace'), location.origin); }
    catch { target = new URL('/workspace', location.origin); }
    if (target.origin !== location.origin) target = new URL('/workspace', location.origin);
    if (task.conversation_id && task.kind === 'chat') target.searchParams.set('conversation_id', task.conversation_id);
    return target.pathname + target.search + target.hash;
  }
  function paint() {
    if (!center) return;
    const tasks = [...records.values()].filter(t => !t.invalidated && t.status !== 'cancelled' && (!terminal(t.status) || !t.acknowledged))
      .sort((a,b) => Number(terminal(a.status))-Number(terminal(b.status)) || Number(b.created_at||b.updated_at||0)-Number(a.created_at||a.updated_at||0));
    center.hidden = !tasks.length;
    const active = tasks.filter(t => !terminal(t.status)).length;
    const failed = tasks.filter(t => ['failed','interrupted'].includes(t.status)).length;
    const description=active ? `${active} 项进行中，可以继续浏览` : failed ? `${failed} 项需要处理` : `${tasks.length} 项新结果可查看`;
    const label=summary.querySelector('.chat-task-summary-label'),count=summary.querySelector('.chat-task-summary-count');
    const labelText=center.dataset.placement==='home'?'当前任务':'回复';
    const countText=center.dataset.placement==='home'?description:String(tasks.length);
    if(label && label.textContent!==labelText)label.textContent=labelText;
    if(count && count.textContent!==countText)count.textContent=countText;
    summary.title=description;summary.setAttribute('aria-label','后台任务：'+description);
    center.dataset.state=active?'running':failed?'failed':'completed';
    // Do not replace focused actions on each polling tick.
    const signature = tasks.map(t => [t.id, t.status, t.title, taskURL(t)].join('|')).join(';');
    if (list.dataset.signature === signature) return;
    list.dataset.signature = signature; list.replaceChildren();
    const labels = {submitting: '正在确认发送', queued: '排队中', running: '正在回复', cancelling: '正在停止',
      completed: '回复已完成', failed: '回复失败', cancelled: '已停止', interrupted: '生成已中断'};
    for (const task of tasks.slice(0, 20)) {
      const row = document.createElement('div'); row.dataset.chatTask = task.id;
      const link = document.createElement('a'); link.href = taskURL(task);
      link.textContent = `${task.title || '助教回复'} · ${labels[task.status] || '正在恢复连接'}`;
      const description=document.createElement('div');description.className='chat-task-description';
      const time=document.createElement('small');time.dataset.taskTime=task.id;
      description.append(link,time);row.append(description);
      const button = document.createElement('button'); button.type = 'button';
      button.textContent = terminal(task.status) ? '知道了' : '停止生成';
      button.disabled = ['submitting', 'cancelling'].includes(task.status);
      button.onclick = async () => {
        if (terminal(task.status)) { acknowledge(task.id); return; }
        button.disabled = true;
        try { await cancel(task); } catch (error) { button.disabled = false; link.textContent = error.message; }
      };
      row.append(button); list.append(row);
    }
    paintTimes();
  }
  async function json(url, options = {}) {
    const response = await nativeFetch(url, options);
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      const error = new Error(typeof body.detail === 'string' ? body.detail : '任务读取失败（' + response.status + '）');
      error.status = response.status; throw error;
    }
    return response.json();
  }
  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const control=new AbortController(),timeout=setTimeout(()=>control.abort(),4000);
      let result;
      try{result = await json('/api/chat/tasks?active_only=false&include_request=false',{signal:control.signal,cache:'no-store'});lastTaskBeat=Date.now();}
      finally{clearTimeout(timeout);}
      for (const task of result.tasks || []) remember(task, {}, false);
      persist(); paint();
      return result.tasks || [];
    })();
    try { return await refreshing; } finally { refreshing = null; }
  }
  async function submitOnce(endpoint, body, metadata = {}) {
    const kind = endpoint === '/api/solve/chat' ? 'solve' : /\/step-chat$/.test(endpoint) ? 'step-chat' : 'chat';
    const client_message_id = body.client_message_id || uuid();
    const request = {...body, kind, client_message_id, ...metadata};
    if (kind === 'step-chat') request.problem_id = decodeURIComponent(endpoint.split('/')[3]);
    remember({id: client_message_id, client_message_id, kind, status: 'submitting', conversation_id: body.conversation_id}, metadata);
    const payload = JSON.stringify(request);
    try {
      // keepalive lets a small submission finish when the user immediately follows a link.
      const task = await json('/api/chat/tasks', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: payload, keepalive: new TextEncoder().encode(payload).length < 60000});
      return remember(task, metadata);
    } catch (error) {
      if (error.status) { records.delete(client_message_id); persist(); paint(); throw error; }
      // A lost POST response is ambiguous. Look up the same ID; never repeat a model submission.
      try {
        const found = await json('/api/chat/tasks?client_message_id=' + encodeURIComponent(client_message_id) + '&active_only=false');
        if (found.tasks?.length) return remember(found.tasks[0], metadata);
      } catch { /* The normal monitor will discover it when connectivity returns. */ }
      throw new Error('发送状态尚未确认，请恢复连接后查看后台任务；请勿重复发送同一条消息。');
    }
  }
  function submit(endpoint, body, metadata = {}) {
    const id = body.client_message_id || uuid();
    if (submissions.has(id)) return submissions.get(id).promise;
    const promise = submitOnce(endpoint, {...body, client_message_id: id}, metadata);
    submissions.set(id, {conversationId: body.conversation_id, promise});
    promise.finally(() => submissions.delete(id)).catch(() => {});
    return promise;
  }
  async function find({conversationId, endpoint, clientMessageId, includeAcknowledged = false} = {}) {
    // A rapid step switch may beat the POST acknowledgement. Await that same submission, not a new one.
    const pending = [...submissions.values()].filter(item => conversationId && item.conversationId === conversationId);
    if (pending.length) await Promise.all(pending.map(item => item.promise));
    const params = new URLSearchParams({active_only: 'false'});
    if (conversationId) params.set('conversation_id', conversationId);
    if (clientMessageId) params.set('client_message_id', clientMessageId);
    const result = await json('/api/chat/tasks?' + params);
    const tasks = (result.tasks || []).map(t => remember(t, {}, false)).filter(t => !t.invalidated &&
      (!conversationId || t.conversation_id === conversationId) &&
      (!endpoint || t.kind === (endpoint === '/api/solve/chat' ? 'solve' : /\/step-chat$/.test(endpoint) ? 'step-chat' : 'chat')) &&
      (includeAcknowledged || !terminal(t.status) || !t.acknowledged));
    persist(); paint();
    if (!tasks.length && [...records.values()].some(t => t.status === 'submitting' && t.conversation_id === conversationId)) {
      throw new Error('上一条消息的发送状态仍待确认，请稍后重新读取；后台任务不会重复提交。');
    }
    tasks.sort((a, b) => Number(b.created_at || b.updated_at || 0) - Number(a.created_at || a.updated_at || 0));
    return tasks[0] || null;
  }
  async function cancel(task) {
    const result = await json('/api/chat/tasks/' + encodeURIComponent(task.id) + '/cancel', {method: 'POST'});
    if (result.id) remember(result); else await refresh();
    return result;
  }
  function acknowledge(id) {
    const task = records.get(id); if (task) { task.acknowledged = true; persist(); paint(); }
  }
  function delay(ms, signal) {
    return new Promise((resolve, reject) => {
      if (signal.aborted) { reject(new DOMException('Detached', 'AbortError')); return; }
      const abort = () => { clearTimeout(handle); reject(new DOMException('Detached', 'AbortError')); };
      const handle = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
      signal.addEventListener('abort', abort, {once: true});
    });
  }
  async function response(task, {signal, after = 0} = {}) {
    const control = new AbortController();
    const detach = () => control.abort();
    if (signal?.aborted) detach(); else signal?.addEventListener('abort', detach, {once: true});
    // At most one subscriber per task in this page. Another page may independently read it.
    subscriptions.get(task.id)?.abort(); subscriptions.set(task.id, control);
    const encoder = new TextEncoder();
    const body = new ReadableStream({
      async start(output) {
        let cursor = Number(after) || 0, ended = false, sawError = false;
        const emit = entry => {
          if (Number(entry.id) <= cursor) return;
          cursor = Number(entry.id);
          if (entry.event?.type === 'error') sawError = true;
          output.enqueue(encoder.encode('data: ' + JSON.stringify(entry.event) + '\n\n'));
        };
        const base = '/api/chat/tasks/' + encodeURIComponent(task.id);
        try {
          // Fetch SSE supports existing auth/cookies. A stalled/broken stream degrades to read-only polling.
          const streamControl = new AbortController();
          const abortStream = () => streamControl.abort();
          const hiddenStream = () => { if (document.hidden) abortStream(); };
          control.signal.addEventListener('abort', abortStream, {once: true});
          document.addEventListener('visibilitychange', hiddenStream);
          let watchdog;
          const resetWatchdog = () => { clearTimeout(watchdog); watchdog = setTimeout(abortStream, 25000); };
          try {
            if (control.signal.aborted) throw new DOMException('Detached', 'AbortError');
            // Completed replays and additional hidden step panels use short GETs. HTTP/1.1
            // otherwise allows a few long SSE readers to exhaust all navigation connections.
            if (terminal(task.status) || document.hidden || subscriptions.size > 1) throw new Error('Use polling');
            resetWatchdog();
            const live = await nativeFetch(base + '/events?after=' + cursor, {signal: streamControl.signal, headers: {Accept: 'text/event-stream'}});
            if (!live.ok || !live.body?.getReader || !/text\/event-stream/i.test(live.headers.get('content-type') || '')) throw new Error('Use polling');
            const reader = live.body.getReader(), decoder = new TextDecoder();
            let buffer = '';
            const parse = block => {
              const lines = block.split('\n');
              const id = lines.find(line => line.startsWith('id:'))?.slice(3).trim();
              const data = lines.filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
              if (id && data) emit({id, event: JSON.parse(data)});
            };
            try {
              while (true) {
                const chunk = await reader.read(); resetWatchdog();
                buffer += chunk.done ? decoder.decode() : decoder.decode(chunk.value, {stream: true});
                // Preserve CRLF split across network chunks.
                buffer = buffer.replace(/\r\n/g, '\n');
                const parts = buffer.split('\n\n'); buffer = parts.pop();
                for (const block of parts) parse(block);
                if (chunk.done) { if (buffer.trim()) parse(buffer); break; }
              }
            } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
          } catch (error) {
            if (control.signal.aborted) throw error;
          } finally {
            clearTimeout(watchdog); streamControl.abort(); control.signal.removeEventListener('abort', abortStream);
            document.removeEventListener('visibilitychange', hiddenStream);
          }
          let backoff = 800;
          while (!control.signal.aborted) {
            try {
              const snapshot = await json(base + '?after=' + cursor, {signal: control.signal});
              for (const entry of snapshot.events || []) emit(entry);
              remember(snapshot);
              if (terminal(snapshot.status) && cursor >= Number(snapshot.cursor || 0)) {
                if (!sawError && ['failed', 'interrupted'].includes(snapshot.status)) output.enqueue(encoder.encode('data: ' + JSON.stringify({type: 'error', message: snapshot.error || '生成已中断，已有内容已保留。'}) + '\n\n'));
                ended = true; break;
              }
              backoff = 800;
              if (snapshot.events?.length && cursor < Number(snapshot.cursor || 0)) continue;
            } catch (error) {
              if (control.signal.aborted || [400, 401, 403, 404, 410].includes(error.status)) throw error;
              backoff = Math.min(backoff * 2, 10000);
            }
            await delay(document.hidden ? Math.max(backoff, 5000) : backoff, control.signal);
          }
          if (!ended) throw new DOMException('Detached', 'AbortError');
          output.close();
        } catch (error) { output.error(error); }
        finally {
          signal?.removeEventListener('abort', detach);
          if (subscriptions.get(task.id) === control) subscriptions.delete(task.id);
        }
      },
      cancel() { control.abort(); },
    });
    return new Response(body, {headers: {'Content-Type': 'text/event-stream'}});
  }
  // Batch expensive Markdown/KaTeX work while displaying lightweight text during bursts.
  function renderer(render, interval = 100) {
    let pending = null, timer;
    const flush = () => { clearTimeout(timer); timer = null; if (pending) { const [element, text] = pending; pending = null; if (element.isConnected) render(element, text); } };
    return {update(element, text) { pending = [element, text]; if (!timer) timer = setTimeout(flush, document.hidden ? 1000 : interval); }, flush,
      cancel() { clearTimeout(timer); timer = null; pending = null; }};
  }
  function mount() {
    const style = document.createElement('link'); style.rel = 'stylesheet'; style.href = '/static/chat-tasks.css?v=heartbeat1'; document.head.append(style);
    center = document.createElement('details'); center.id = 'chat-task-center'; center.hidden = true;
    summary = document.createElement('summary'); summary.id = 'chat-task-summary'; summary.setAttribute('aria-live', 'polite');
    summary.innerHTML='<span class="chat-task-summary-label">回复</span><span class="chat-task-summary-count"></span><span class="chat-task-summary-action" aria-hidden="true">查看 <span>⌄</span></span>';
    list = document.createElement('div'); list.className = 'chat-task-list'; center.append(summary, list); document.body.append(center); paint();
    // The home owns one inline task row. Other pages dock into the ready shell.
    const dock=()=>{
      const home=document.getElementById('home-tasks');
      const target=home||document.querySelector('.ui-header[data-research-ui-ready="true"] .ui-header-end');
      if(!target)return;
      if(center.parentNode!==target)target.prepend(center);
      center.dataset.docked='true';center.dataset.placement=home?'home':'header';paint();
    };
    document.addEventListener('research-ui-ready',dock);
    window.ResearchUI?.ready?.then(dock);
    dock();
    document.addEventListener('click', event => { if (!center.contains(event.target)) center.open = false; });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && center.open) { center.open = false; summary.focus(); } });
    monitor();
    clockTick();
  }
  async function monitor() {
    const epoch = ++monitorEpoch;
    clearTimeout(timer); if (stopped) return;
    try { await refresh(); } catch { /* No global error overlay; keep last known task references. */ }
    if (!stopped && epoch === monitorEpoch) timer = setTimeout(monitor, document.hidden ? 15000 : [...records.values()].some(t => !terminal(t.status)) ? 2000 : 10000);
  }
  window.addEventListener('storage', event => { if (event.key === key) { for (const item of readCache()) if (item?.id) records.set(item.id, {...records.get(item.id), ...item}); paint(); } });
  window.addEventListener('online', () => monitor());
  document.addEventListener('visibilitychange', () => { if (!document.hidden) monitor(); });
  window.addEventListener('pagehide', () => { stopped = true; clearTimeout(timer); clearTimeout(clockTimer); for (const control of subscriptions.values()) control.abort(); });
  window.addEventListener('pageshow', event => { if (event.persisted) { stopped = false; monitor(); clockTick(); } });
  window.ChatTasks = {submit, response, find, cancel, acknowledge, refresh, terminal, uuid, renderer, taskURL, watch, duration, ready: Promise.resolve()};
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once: true}); else mount();
})();
