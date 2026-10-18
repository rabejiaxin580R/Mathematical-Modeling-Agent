/* Shared navigation and accessible native controls. Business state stays in page modules. */
(function () {
  'use strict';
  if (!document.documentElement.dataset.guidePreview && !window.OnboardingTour && !document.querySelector('script[src*="onboarding.js"]')) {
    const guide = document.createElement('script'); guide.src = '/static/onboarding.js?v=policy3'; document.head.append(guide);
  }
  const paths={mark:'M4 17 10 5l5 9 5-7M4 17h16M10 5v12',plus:'M12 5v14M5 12h14',arrow:'M5 12h14m-5-5 5 5-5 5',folder:'M3 7h7l2 3h9v10H3zM3 7V4h7l2 3',book:'M12 5v15M3 4h5l4 2 4-2h5v15h-5l-4 2-4-2H3z',check:'m5 12 4 4L19 6',code:'m8 6-6 6 6 6m8-12 6 6-6 6m-3-14-2 16',menu:'M4 6h16M4 12h16M4 18h16',close:'m6 6 12 12M18 6 6 18'};
  const icon=(name)=>`<svg class="ui-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${paths[name]||paths.mark}"/></svg>`;
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let resolveReady;
  const ready=new Promise(resolve=>{resolveReady=resolve;});
  function publishReady(header){
    if(header)header.dataset.researchUiReady='true';
    resolveReady(header);
    document.dispatchEvent(new CustomEvent('research-ui-ready',{detail:{header}}));
  }
  window.ResearchUI={icon,esc,ready, async needModel(){
    try { if((await (await fetch('/api/onboarding')).json()).has_api_key)return true; } catch { return true; }
    if(typeof KeySetup==='undefined')return true;
    return new Promise(resolve=>KeySetup.open(async()=>{try {resolve(!!(await (await fetch('/api/onboarding')).json()).has_api_key);}catch{resolve(false);}}));
  }, image(src,caption){
    const d=document.createElement('dialog');d.className='ui-image-dialog';d.innerHTML=`<button autofocus aria-label="关闭图表">${icon('close')}</button><img src="${esc(src)}" alt="${esc(caption)}"><p>${esc(caption)}</p>`;
    const origin=document.activeElement;d.querySelector('button').onclick=()=>d.close();d.addEventListener('click',e=>{if(e.target===d)d.close();});d.onclose=()=>{d.remove();origin?.focus();};document.body.append(d);d.showModal();
  }};
  function mount(){
    const page=document.documentElement.dataset.guidePage||location.pathname.split('/').filter(Boolean).pop()||'landing';document.body.dataset.page=page;document.body.classList.add('ui-v2');
    document.querySelectorAll('.home-mark,.landing-mark,.fm-mark,.ks-mark,.sv-brand .mark,.ws-brand .mark,.learn-mark,.practice-mark').forEach(n=>{n.innerHTML=icon('mark');n.classList.add('ui-mark');});
    if(page==='landing'){publishReady(null);return;}
    const work=['solve','free','ide'].includes(page);
    const header=document.createElement('header');header.className='ui-header';
    header.innerHTML=`<a class="ui-brand" href="/home" aria-label="建模研究台，返回项目"><span class="ui-mark">${icon('mark')}</span><span>建模研究台</span></a>${work?'<a class="ui-back" href="/home">返回项目</a>':`<nav class="ui-nav" aria-label="主导航">${[['home','项目','/home'],['learn','学习','/learn'],['practice','练习','/practice'],['outline','论文','/outline?history=1']].map(([key,label,url])=>`<a href="${url}" ${(page===key||key==='outline'&&page==='paper')?'aria-current="page"':''}>${label}</a>`).join('')}</nav>`}<div class="ui-header-end"></div><details class="ui-menu"><summary aria-label="更多功能">${icon('menu')}<span>更多</span></summary><div class="ui-menu-items"><a href="/build">工具</a><a href="/assessment">学习测评</a><button id="ui-model-settings">模型连接</button><button id="ui-profile-settings">切换档案</button></div></details>`;
    document.body.prepend(header);
    header.querySelector('#ui-model-settings').onclick=async()=>{if(typeof KeySetup==='undefined')await new Promise((resolve,reject)=>{const s=document.createElement('script');s.src='/static/keysetup.js';s.onload=resolve;s.onerror=reject;document.head.append(s);});KeySetup.open();};
    header.querySelector('#ui-profile-settings').onclick=()=>{if(typeof Profile!=='undefined')Profile.clear();location.href='/';};
    const end=header.querySelector('.ui-header-end');
    const user=document.getElementById('home-user');if(user){end.append(user);if(page==='build')new MutationObserver(()=>{user.querySelectorAll('button').forEach(b=>b.remove());}).observe(user,{childList:true});}
    if(page==='ide'){const environment=document.createElement('details');environment.className='ui-menu';environment.innerHTML='<summary>工作环境</summary><div class="ui-menu-items"></div>';document.querySelector('.ide-top').prepend(environment);environment.querySelector('div').append(document.getElementById('workdir'));}
    const options=document.getElementById('home-options');if(options){header.querySelector('.ui-menu-items').append(options);options.className='ui-more-options';}
    // Settings and layout tools belong in the secondary menu. Moving nodes preserves listeners.
    if(page==='free'){const tabs=document.createElement('div');tabs.className='ui-segment workspace-tabs';tabs.innerHTML='<button data-panel=code aria-pressed=true>代码</button><button data-panel=files aria-pressed=false>文件</button><button data-panel=chat aria-pressed=false>助教对话</button>';document.querySelector('.ws-topbar').after(tabs);document.body.dataset.toolPanel='code';tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>{document.body.dataset.toolPanel=b.dataset.panel;tabs.querySelectorAll('button').forEach(n=>n.setAttribute('aria-pressed',n===b));window.IDE?.editor.refresh();});}
    if(work){['model-selector','btn-settings','sv-work-environment'].forEach(id=>{const n=document.getElementById(id);if(n)header.querySelector('.ui-menu-items').append(n);});}
    if(page==='solve'){
      const toolbar=document.createElement('div');toolbar.className='chat-model-tools';
      const menu=document.createElement('details');menu.className='ui-menu chat-api-menu';
      menu.innerHTML='<summary aria-label="模型与 API 设置" title="模型与 API 设置">⋯</summary><div class="ui-menu-items"></div>';
      menu.querySelector('div').append(document.getElementById('ui-model-settings'));
      const settings=document.getElementById('btn-settings');if(settings){settings.textContent='API 密钥 / 自定义模型';menu.querySelector('div').append(settings);}
      menu.querySelector('#ui-model-settings').onclick=()=>KeySetup.open(async()=>{
        try{const s=await (await fetch('/api/settings')).json();document.getElementById('model-badge').textContent=s.model||'';}catch{}
      });
      toolbar.append(document.getElementById('model-selector'),menu);
      document.querySelector('.sv-composer').before(toolbar);
    }
    const viewport=window.visualViewport;if(viewport){const resize=()=>{document.documentElement.style.setProperty('--visual-height',viewport.height+'px');};viewport.addEventListener('resize',resize);resize();}
    document.querySelectorAll('button[title]:not([aria-label])').forEach(b=>{if(b.textContent.trim().length<3)b.setAttribute('aria-label',b.title);});
    document.querySelectorAll('input:not([type=hidden]),textarea,select').forEach(n=>{if(!n.labels?.length&&!n.getAttribute('aria-label'))n.setAttribute('aria-label',n.placeholder||n.title||n.id);});
    const modalSelector='.settings-overlay,.fm-overlay,.concept-reader,.ks-overlay,.asm-pathpanel';
    let modal=null,origin=null,lastOutside=null;
    document.addEventListener('focusin',e=>{if(!e.target.closest(modalSelector))lastOutside=e.target;});
    const checkModal=()=>{const next=[...document.querySelectorAll(modalSelector)].filter(n=>!n.hidden&&getComputedStyle(n).display!=='none').at(-1)||null;if(next===modal)return;if(modal&&!next){origin?.isConnected&&origin.focus();modal=null;return;}if(next){origin=lastOutside||document.activeElement;modal=next;modal.setAttribute('role','dialog');modal.setAttribute('aria-modal','true');const heading=modal.querySelector('h1,h2,.fm-title');if(heading)modal.setAttribute('aria-label',heading.textContent);if(!modal.contains(document.activeElement))modal.querySelector('button,input,textarea,[tabindex="0"]')?.focus();}};
    new MutationObserver(checkModal).observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:['hidden','class','style']});
    document.addEventListener('keydown',e=>{if(!modal||e.target.closest('.wait-hub'))return;const controls=[...modal.querySelectorAll('button:not(:disabled),a[href],input:not(:disabled),textarea:not(:disabled),select:not(:disabled),summary,[tabindex="0"]')].filter(n=>n.getClientRects().length);if(e.key==='Tab'&&controls.length){if(e.shiftKey&&document.activeElement===controls[0]){e.preventDefault();controls.at(-1).focus();}else if(!e.shiftKey&&document.activeElement===controls.at(-1)){e.preventDefault();controls[0].focus();}}if(e.key==='Escape')modal.querySelector('#btn-close-settings,#fm-skip,#reader-close,#ks-x,#asm-pathpanel-close')?.click();});
    document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelectorAll('.ui-menu[open]').forEach(d=>{d.open=false;d.querySelector('summary').focus();});});
    document.addEventListener('click',e=>document.querySelectorAll('.ui-menu[open]').forEach(d=>{if(!d.contains(e.target))d.open=false;}));
    window.ResearchCompanion?.addSettings(header.querySelector('.ui-menu-items'));
    publishReady(header);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount);else mount();
})();
