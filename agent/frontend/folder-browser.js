/* Local workspace picker. Browsing never changes the active workspace. */
window.FolderBrowser = (() => {
  let pending, resolve, current, busy = false, previousFocus, generation = 0;
  const $ = id => document.getElementById(id);
  const dialog = () => $('fm-overlay');
  function setBusy(value) {
    busy = value;
    $('fm-use').disabled = value || !current;
    $('fm-up').disabled = value || !current || current.path === current.parent;
    $('fm-new').disabled = value || !current;
    $('fm-create').disabled = value;
    $('fm-list').setAttribute('aria-busy', String(value));
  }
  function button(label, action) {
    const b = document.createElement('button'); b.type = 'button'; b.textContent = label; b.onclick = action; return b;
  }
  async function api(url, data) {
    const response = await fetch(url, data ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)} : {});
    const result = await response.json();
    if (!response.ok || !result.ok) throw Error(result.error || result.detail || '暂时无法读取文件夹，请重试。');
    return result;
  }
  function renderFolders() {
    const query = $('fm-filter').value.toLocaleLowerCase();
    const folders = (current?.folders || []).filter(item => item.name.toLocaleLowerCase().includes(query));
    $('fm-list').replaceChildren(...folders.map(item => {
      const row = button('', () => load(item.path)); row.className = 'fb-folder';
      const icon = document.createElement('span'); icon.textContent = '▱'; icon.setAttribute('aria-hidden','true');
      const name = document.createElement('span'); name.textContent = item.name;
      const arrow = document.createElement('span'); arrow.textContent = '›'; arrow.setAttribute('aria-hidden','true');
      row.append(icon, name, arrow); row.title = '打开 '+item.name; return row;
    }));
    if (!folders.length) { const empty = document.createElement('p'); empty.className='fb-empty'; empty.textContent=query?'没有匹配的文件夹':'这里没有子文件夹，可以直接使用或新建一个。'; $('fm-list').append(empty); }
  }
  async function load(path = '') {
    if (busy) return;
    const token = generation;
    setBusy(true); $('fm-message').textContent='正在读取文件夹…';
    try {
      const data = await api('/api/ide/folders?path='+encodeURIComponent(path));
      if (token !== generation) return;
      current=data; $('fm-filter').value=''; $('fm-create-form').hidden=true;
      $('fm-location').textContent=data.path;
      $('fm-crumbs').replaceChildren(...data.breadcrumbs.map(item=>button(item.name,()=>load(item.path))));
      $('fm-locations').replaceChildren(...data.locations.map(item=>{const b=button(item.name,()=>load(item.path));b.setAttribute('aria-current',String(item.path===data.path));return b;}));
      renderFolders(); $('fm-message').textContent=''; $('fm-list').scrollTop=0;
    } catch(error) {
      if(token===generation) {
        if(!current && path) { setBusy(false); await load(); }
        $('fm-message').textContent=error.message;
      }
    }
    finally { if(token===generation)setBusy(false); }
  }
  function close(value = null) {
    generation++; busy=false; dialog().close(); const done=resolve; pending=null; resolve=null; done?.(value); previousFocus?.focus();
  }
  function open(path = '') {
    if (pending) return pending;
    pending = new Promise(done=>{resolve=done;}); previousFocus=document.activeElement; current=null; generation++;
    $('fm-location').textContent='尚未选择'; $('fm-list').replaceChildren(); $('fm-crumbs').replaceChildren();
    $('fm-create-form').hidden=true; dialog().showModal();
    load(path); return pending;
  }
  document.addEventListener('DOMContentLoaded', () => {
    dialog().addEventListener('cancel',event=>{event.preventDefault();close();});
    $('fm-cancel').onclick=()=>close(); $('fm-close').onclick=()=>close();
    $('fm-up').onclick=()=>load(current.parent);
    $('fm-filter').oninput=renderFolders;
    $('fm-new').onclick=()=>{$('fm-create-form').hidden=false;$('fm-name').value='';$('fm-name').focus();};
    $('fm-new-cancel').onclick=()=>{$('fm-create-form').hidden=true;$('fm-new').focus();};
    $('fm-create-form').onsubmit=async event=>{
      event.preventDefault(); if(busy || !current)return;
      const token=generation; setBusy(true);
      try {
        const data=await api('/api/ide/folders',{parent:current.path,name:$('fm-name').value});
        if(token!==generation)return;
        setBusy(false); await load(data.path);
      } catch(error) {if(token===generation)$('fm-message').textContent=error.message;}
      finally {if(token===generation)setBusy(false);}
    };
    $('fm-use').onclick=async()=>{
      if(busy || !current)return;
      const token=generation; setBusy(true);
      try { const data=await api('/api/ide/folders?path='+encodeURIComponent(current.path)); if(token===generation)close(data.path); }
      catch(error) {if(token===generation)$('fm-message').textContent=error.message;}
      finally {if(token===generation)setBusy(false);}
    };
  });
  return {open};
})();
