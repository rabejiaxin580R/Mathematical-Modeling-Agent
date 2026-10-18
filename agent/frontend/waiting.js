/* Request lifetimes shared with research feedback. No global popovers or autoplay. */
(function () {
  'use strict';
  if (window.MMWait) return;
  const nativeFetch = window.fetch.bind(window), tasks = new Map(), listeners = new Set();
  let sequence = 0, ticker = null;
  function labelFor(path) {
    if (/summarize/.test(path)) return '整理讨论草稿';
    if (/generate-full/.test(path)) return '生成论文章节';
    if (/regenerate|resume|\/paper\/outline$/.test(path)) return '整理论文大纲';
    if (/analyze/.test(path)) return '读题与拆解';
    if (/step-chat|\/chat$/.test(path)) return '助教回复';
    if (/upload/.test(path)) return '上传与解析文件';
    if (/\/run$/.test(path)) return '运行代码';
    return null;
  }
  const snapshot = () => [...tasks.values()].map(task => ({...task, seconds: Math.floor((performance.now()-task.start)/1000)}));
  function paint() { const current = snapshot(); for (const listener of listeners) {try {listener(current);} catch(error) {console.warn('Waiting view:',error.message);}} }
  function updateClock() {
    if (ticker) {clearInterval(ticker);ticker=null;}
    if (tasks.size && !document.hidden) ticker=setInterval(paint,1000);
  }
  function begin(label, options = {}) {
    if (!label) return () => {};
    const id=++sequence;tasks.set(id,{id,label,start:performance.now(),cancel:options.cancel || null});paint();updateClock();
    let ended=false;
    return function finish(){if(ended)return;ended=true;tasks.delete(id);paint();updateClock();};
  }
  document.addEventListener('visibilitychange',()=>{updateClock();paint();});
  // Preserve the original Response and stream; do not buffer or eagerly consume SSE.
  window.fetch = async function (input, options) {
    let url;
    try {url = new URL(input instanceof Request ? input.url : input, location.href);} catch {return nativeFetch(input, options);}
    if (url.origin !== location.origin || (!url.pathname.startsWith('/api/') && url.pathname !== '/learn')) return nativeFetch(input, options);
    const signal = options?.signal || (input instanceof Request ? input.signal : null);
    const stop = begin(labelFor(url.pathname, String(options?.method || (input instanceof Request ? input.method : 'GET')).toUpperCase()));
    let fallback;
    const finish = () => {clearTimeout(fallback);signal?.removeEventListener('abort', finish);stop();};
    signal?.addEventListener('abort', finish, {once:true});
    try {
      const response = await nativeFetch(input, options);
      if (!response.body) {finish();return response;}
      // Some callers only check HTTP status and deliberately leave the body unread.
      fallback = setTimeout(finish, 0);
      for (const method of ['json', 'text', 'blob', 'arrayBuffer', 'formData']) {
        const consume = response[method].bind(response);
        response[method] = async (...args) => {clearTimeout(fallback);try{return await consume(...args);}finally{finish();}};
      }
      const stream = response.body, getReader = stream.getReader.bind(stream);
      stream.getReader = (...args) => {
        clearTimeout(fallback);
        let reader;
        try {reader = getReader(...args);} catch (error) {finish();throw error;}
        const read = reader.read.bind(reader), cancel = reader.cancel.bind(reader), release = reader.releaseLock.bind(reader);
        reader.read = async (...params) => {
          try {const chunk = await read(...params);if (chunk.done) finish();return chunk;}
          catch (error) {finish();throw error;}
        };
        reader.cancel = async (...params) => {try{return await cancel(...params);}finally{finish();}};
        reader.releaseLock = () => {try{return release();}finally{finish();}};
        return reader;
      };
      return response;
    } catch (error) {finish();throw error;}
  };

  window.MMWait = {begin, snapshot, subscribe(listener){listeners.add(listener);listener(snapshot());return ()=>listeners.delete(listener);}};
})();
