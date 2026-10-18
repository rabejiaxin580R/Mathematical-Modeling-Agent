/* Demo data only. The host strips all business entrypoints and denies network writes. */
(function () {
  'use strict';
  if (!document.documentElement.dataset.guidePreview) return;
  const number=Number(document.documentElement.dataset.guideStep);
  const page=document.documentElement.dataset.guidePage;
  const $=id=>document.getElementById(id), md=(node,text)=>MMRender.renderMarkdown(node,text);
  const title='救护站选址：覆盖与响应时间（模拟数据）';
  const names=['问题分析与重述','模型假设','符号与变量','模型建立','模型求解','结果分析与检验','灵敏度分析','模型评价','模型推广与写作'];
  const statement='从 A、B、C、D 四个候选站点选择两个救护站。四个区域人口权重为 50、30、15、5；每个区域都必须在 8 分钟内到达。在此条件下，最小化人口加权平均响应时间。所有数值均为模拟数据。';
  const thought='我想先选择 A 和 B，人口多的区域响应快，但不确定能否兼顾全部区域。';
  const code='from itertools import combinations\n\ntimes = [[4,7,12,14], [8,3,9,12],\n         [12,8,4,7], [14,11,7,3]]\nweights = [50, 30, 15, 5]\n\nfor a, b in combinations(range(4), 2):\n    response = [min(times[a][i], times[b][i])\n                for i in range(4)]\n    mean = sum(w*t for w,t in zip(weights,response))/100\n    print(chr(65+a)+chr(65+b), max(response), mean)';
  const output='方案   最长时间   加权平均\nA+B    12 分钟     4.85 分钟（不满足覆盖）\nA+C     7 分钟     5.05 分钟\nA+D     7 分钟     5.30 分钟\nB+C     8 分钟     5.85 分钟\nB+D     8 分钟     6.10 分钟\nC+D    12 分钟     9.15 分钟（不满足覆盖）';
  const comparison='| 方案 | 最长响应时间 | 加权平均 | 满足 8 分钟覆盖 |\n|---|---:|---:|---|\n| A+B | 12 分钟 | 4.85 分钟 | 否 |\n| A+C | 7 分钟 | 5.05 分钟 | 是 |\n| A+D | 7 分钟 | 5.30 分钟 | 是 |\n| B+C | 8 分钟 | 5.85 分钟 | 是 |\n| B+D | 8 分钟 | 6.10 分钟 | 是 |';
  const actions=new Map();
  function action(node,name,fn) { if(!node)return;node.dataset.guideAction=name;actions.set(name,fn); }
  function emit() { document.dispatchEvent(new Event('guide-action')); }
  function show(node,on=true) { if(!node)return;node.hidden=!on;node.style.display=on?'':'none'; }
  function editor() {
    if(!$('editor'))return;
    const ed=CodeMirror.fromTextArea($('editor'),{mode:'python',lineNumbers:true,readOnly:'nocursor',lineWrapping:true});ed.setValue(code);
    window.IDE={editor:ed,isRunning:()=>false};
    if($('con-log'))$('con-log').textContent=output;
    if($('con-status'))$('con-status').textContent='预置结果';
  }
  function home() {
    $('greet').textContent='我的项目';
    $('home-feature').innerHTML=`<article class="ui-project-feature"><div><div class="ui-kicker">示例项目 · 模拟数据</div><h2>${title}</h2><p>下一步：检查候选方案是否满足覆盖条件</p><p>已确认 4 / 9 步</p><div class="ui-progress-track"><i style="width:44%"></i></div></div><a class="ui-btn primary">继续项目 →</a></article>`;
    if(number===25) {
      document.querySelector('.home-main').innerHTML=`<section class="guide-preview-settings"><h2>模型连接与分工</h2><div id="guide-model-options" class="ui-segment"><button aria-pressed="true">使用提供的服务</button><button>使用自己的接口</button></div><div id="guide-model-fields"><label>API 密钥</label><input type="password" value="example-key-not-real" readonly><label>接口地址</label><input value="由所选服务提供" readonly><label>模型</label><input value="按服务选择" readonly></div><div id="guide-role-options"><label>分工偏好</label><div class="ui-segment"><button>自动</button><button>编程</button><button>建模</button><button>写作</button></div></div></section>`;
    }
  }
  function solve() {
    editor(); $('sv-statement').value=statement;$('sv-thought').value=thought;
    $('workdir').value='示例工作目录';show($('fm-overlay'),false);
    if(number===3)return;
    show($('sv-setup'),false);show($('sv-tabs'));show($('sv-cockpit'));$('sv-cur-title').textContent=title;
    $('sv-rail-title').textContent='救护站选址';$('sv-save-state').textContent='示例';
    const steps=names.map((name,i)=>({id:'s'+(i+1),title:name,modality:i===4?'code':'prose',prompt:'检查条件，记录依据与限制。'}));
    const problem={title,steps};
    $('sv-roadmap').innerHTML=steps.map((s,i)=>`<button class="sv-step ${i===4?'cur':''}" data-idx="${i}"><span class="sv-step-num">${i+1}</span><span class="sv-step-body"><span class="sv-step-title">${s.title}</span></span></button>`).join('');
    $('sv-step-head').innerHTML='<h2>模型求解</h2><p>候选方案能否在 8 分钟内覆盖所有区域？</p>';
    $('sv-chat').innerHTML=`<div class="guide-demo-message user">${thought}</div><div class="guide-demo-message">先检查最远区域的响应时间，再比较满足约束的方案。</div>`;
    $('sv-input').placeholder='说说你想检查的条件…';$('sv-input').disabled=false;$('sv-send').disabled=false;
    ['filename','btn-save','btn-run','btn-stop'].forEach(id=>{if($(id))$('sv-editor-tools').append($(id));});
    const data={initial_thought:thought,revision:1,outcomes:{},evidence:[{id:'example-evidence',step_id:'s5',purpose:'比较所有候选组合的覆盖与加权响应时间',source:'simulation',status:'success',created_at:1789603200,stdout:output,code,images:[],inputs:[],outcome_version:1}]};
    for(const [i,s] of steps.entries())data.outcomes[s.id]={status:(number===10||i<4)?'confirmed':'draft',version:1,decision:i===4?'选择 A+C 作为当前模拟条件下的方案':s.title+'：示例研究记录',reason:'满足全部区域 8 分钟内到达的约束，并比较可行方案的加权平均。',basis:'枚举 6 个站点组合；A+C 最长响应为 7 分钟，加权平均为 5.05 分钟。',limits:'模拟时间矩阵固定，尚未考虑拥堵和随机需求。',verdict:'supported',evidence_ids:i===4?['example-evidence']:[],history:[]};
    SolveFlow.init({state:()=>({problem,index:4,id:null}),isBusy:()=>false,layout:()=>window.IDE.editor.refresh(),append:()=>{},save:async()=>{},enter:()=>{}});
    SolveFlow.accept(data);
    const view=number>=7&&number<=8?'verification':number>=9?'outcome':'discussion';
    SolveFlow.view(number===9?'discussion':view);
    if(number===10){const note=document.createElement('div');note.id='guide-history';note.className='guide-history-note';note.innerHTML='<strong>历史版本</strong><p>第 1 版：优先考虑 A+B。第 2 版：核对覆盖约束后采用 A+C。</p><p>若修改前面的决定，后续成果需要重新确认。</p>';$('sv-outcome').prepend(note);}
    action(document.querySelector('[data-view="verification"]'),'verification',()=>SolveFlow.view('verification'));
    action(document.querySelector('[data-task="code"]'),'code',()=>{SolveFlow.view('verification');SolveFlow.verificationMode('code',false);});
    action(document.querySelector('[data-view="outcome"]'),'outcome',()=>SolveFlow.view('outcome'));
  }
  function outline() {
    if(number===15){
      show($('stage-outline'),false);$('stage-welcome').classList.add('active');
      const box=document.querySelector('.welcome-box');box.querySelectorAll('.field,.submit-row').forEach(n=>show(n,false));
      box.querySelector('.page-title').textContent='我的论文';box.querySelector('.page-sub').textContent='示例文档';
      $('historySidebar').classList.remove('collapsed');
      $('historyList').innerHTML=`<a class="history-item"><span class="history-topic">${title}</span><span class="history-line"><span class="history-badge full">完整论文</span></span></a><a class="history-item"><span class="history-topic">候选站点的覆盖比较（模拟数据）</span><span class="history-badge">大纲</span></a>`;
      const filters=document.createElement('div');filters.className='ui-filters';filters.innerHTML='<input placeholder="搜索论文题名" readonly><select><option>全部状态</option></select>';$('historyList').before(filters);return;
    }
    show($('stage-welcome'),false);show($('stage-loading'),false);$('stage-outline').classList.add('active');
    $('flow-paper-topic').textContent=title;
    show($('flow-paper-source'));$('flow-paper-source').textContent='来自示例项目 · 已确认研究材料版本 2 · 模拟数据';
    document.querySelector('.outline-source-details').open=true;
    $('sectionsList').innerHTML=['摘要',...names].map((name,i)=>`<div class="section-item ${i===5?'active':''}"><span class="section-num">${i+1}</span><span class="section-name">${name}</span></div>`).join('');
    $('focusTitle').textContent='模型求解';$('focusMeta').textContent='已确认成果 · 示例';
    for(const id of ['focusSummary','focusFormulas','focusCitations','focusActions','versionBar']){const node=$(id);if(node){node.id=id;node.style.display='block';}}
    // Keep the existing IDs used by the production outline page.
    $('focusSummary').style.display='block';md($('focusSummary'),'### 求解方法\n枚举六种站点组合，先排除不满足覆盖约束的方案，再比较人口加权平均响应时间。\n\n### 结果\n'+comparison+'\n\nA+C 在可行组合中取得最低加权平均时间 5.05 分钟。');
    $('focusFormulas').style.display='block';$('formulasList').textContent='加权平均响应时间 = 各区域人口权重与响应时间乘积之和 / 总权重';
    $('focusCitations').style.display='block';$('citationsList').textContent='本例依据预置模拟矩阵计算，未调用外部文献检索。正式论文需核对引用来源。';
    $('focusActions').style.display='block';$('versionBar').style.display='flex';$('versionHint').textContent='当前版本 2';
    $('statsText').textContent='示例研究材料 · 9 步已确认';
  }
  function paper() {
    document.querySelectorAll('.step-card').forEach(n=>show(n,false));show($('resultBox'));$('resultBox').style.display='block';
    const container=document.querySelector('.paper-container'),header=document.createElement('header');header.className='paper-header';
    header.append($('paper-title'));container.before(header);$('paper-title').textContent=title;
    const reading=document.createElement('div');reading.className='paper-reading';const toc=document.createElement('nav');toc.id='paper-toc';toc.className='paper-toc';reading.append(toc,$('paper-preview'));$('resultBox').prepend(reading);
    const toolbar=document.querySelector('.result-actions');container.before(toolbar);
    md($('paper-preview'),'# 救护站选址的覆盖与响应时间比较\n\n> 导览示例，全部数据为模拟数据。\n\n## 摘要\n在所有区域响应时间不超过 8 分钟的条件下，枚举站点组合并比较人口加权平均响应时间。\n\n## 模型与方法\n选择两个站点，各区域由响应时间最短的已选站点服务。人口权重为 50、30、15、5。\n\n## 计算结果\n'+comparison+'\n\n## 结论与限制\nA+C 的最长响应时间为 7 分钟，加权平均为 5.05 分钟。在本例可行组合中加权平均最低。该结果仅适用于给定模拟矩阵，真实交通仍待验证。');
    $('paper-preview').querySelectorAll('h1,h2').forEach((h,i)=>{const a=document.createElement('a');a.textContent=h.textContent;toc.append(a);});
    $('paper-result-heading').textContent='示例论文';$('paper-source').textContent='研究材料版本 2 · 模拟数据';show($('paper-source'));
    $('statSections').textContent='10';$('statPapers').textContent='0';$('statChunks').textContent='0';$('statWords').textContent='示例';
    if(number===13){
      const status=$('statusBox');status.parentElement.hidden=false;status.parentElement.style.display='block';status.style.display='block';
      status.innerHTML='<h2>生成过程示例</h2><div class="guide-progress-lines"><div class="done">问题重述 <span>已完成</span></div><div class="done">模型建立 <span>已完成</span></div><div>模型求解 <span>待继续</span></div></div>';
      $('retryPaper').hidden=false;show($('resultBox'),false);
    }
  }
  const concept={chunk_id:'guide-coverage',title:'约束条件与目标函数',category:'常用建模方法',difficulty:'Beginner',roles:['modeling'],one_liner:'约束决定哪些方案可以选，目标函数用来比较可行方案。',intuition:'先挑出能在八分钟内覆盖全部区域的组合，再从这些组合中选平均响应最快的。',taxonomy_path:['常用建模方法','优化模型'],siblings:[],cases:['救护站选址（模拟案例）']};
  const path={name:'从问题到优化模型',milestones:[{title:'认识变量与约束',desc:'先分清要决定什么、必须满足什么。',concepts:[{id:'guide-coverage',title:concept.title,difficulty:'Beginner'}]}]};
  function learn() {
    const data={modules:[{module_id:'C',name:'常用建模方法',count:3,subcats:[{subcat_id:'optimization',name:'优化模型',concepts:[{id:concept.chunk_id,title:concept.title,difficulty:'Beginner'},{id:'guide-linear',title:'线性规划',difficulty:'Intermediate'},{id:'guide-integer',title:'整数规划',difficulty:'Intermediate'}]}]}]};
    GuideKnowledge.map(data);
    const tabs=document.createElement('div');tabs.className='ui-segment learn-tabs';tabs.innerHTML='<button aria-pressed="true">学习路径</button><button>全部知识</button>';document.querySelector('.learn-scroll').before(tabs);
    show($('asm-path-banner'));$('asm-path-banner').innerHTML='<div class="ui-next-lesson"><div class="ui-kicker">下一项学习 · 示例路径</div><h2>约束条件与目标函数</h2><p>从问题出发，分清哪些方案可行，以及怎样比较它们。</p><button class="ui-btn primary" id="guide-concept">开始学习 →</button></div>';
    const search=document.createElement('input');search.id='knowledge-search';search.placeholder='搜索知识标题';search.readOnly=true;document.querySelector('.learn-controls').prepend(search);
    action($('guide-concept'),'reader',()=>GuideKnowledge.reader(concept));
    document.querySelector('.concept-item').id='guide-concept-list';action($('guide-concept-list'),'reader',()=>GuideKnowledge.reader(concept));
    $('asm-entry').hidden=false;$('asm-entry').textContent='L2 · 我的路径';
  }
  function assessment() {
    const quiz={count:1,questions:[{id:'q1',module_id:'C',difficulty:'Beginner',stem:'救护站选址中，要求每个区域在八分钟内到达。这属于什么？',options:['约束条件','目标函数','求解结果','可视化方式']}]};
    GuideAssessment.intro({quiz,levels:[{level:'L1',name:'入门',tagline:'从基础开始'},{level:'L2',name:'起步',tagline:'认识模型'}]});
    if(number===19)GuideAssessment.result({level:'L2',level_name:'起步（示例）',source:'quiz',total:10,correct:6,score:.6,per_module:{C:.4,E:.5},path,review:[{stem:quiz.questions[0].stem,options:quiz.questions[0].options,answer:0,chosen:1,correct:false,explain:'覆盖要求限制了可选方案，因此属于约束条件。'}]});
  }
  function practice() {
    if(number===20){$('view-list').innerHTML='<h1 class="practice-h1">真题练习</h1><div class="ui-filters"><input placeholder="搜索题名" readonly><select><option>全部年份</option></select><select><option>全部难度</option></select></div><div class="practice-grid"><button class="pcard"><div class="pcard-top"><span class="pcard-year">导览示例</span><span class="pcard-diff">入门</span></div><h2 class="pcard-title">救护站选址与覆盖</h2><div class="pcard-tags">优化模型 · 约束条件</div><div class="pcard-foot">9 步 <span class="pcard-score">未开始</span></div></button></div>';return;}
    show($('view-list'),false);show($('view-problem'));show($('btn-list'));document.body.classList.add('practice-active');
    $('view-problem').innerHTML=`<div class="problem-head"><h1 class="problem-title">救护站选址与覆盖（练习界面示例）</h1><details class="practice-statement"><summary>完整题面与材料</summary><p>${statement}</p></details></div><div class="ui-practice-layout"><nav class="ui-practice-steps">${names.map((n,i)=>`<button aria-current="${i===0?'step':'false'}">${i+1} ${n}</button>`).join('')}</nav><main id="guide-practice-step"></main></div>`;
    StepFlow.renderSteps($('guide-practice-step'),{id:'guide-problem',steps:[{id:'s1',title:'问题分析与重述',modality:'key-points',prompt:'区分本题的目标与约束条件。',hint:'先列出必须满足的条件，再考虑怎样比较可行方案。'}]},{profileId:'guide',stepState:{}});
    const result=document.querySelector('.pstep-result');result.hidden=false;result.innerHTML='<div class="guide-demo-message"><strong>评估示例：基本掌握</strong><p>已经区分覆盖约束与响应时间目标，还需明确各区域的人口权重。</p></div>';
  }
  function workspace() {
    editor();show($('fm-overlay'),false);$('workdir').value='示例工作目录';
    if(page==='free'){
      $('explorer-tree').innerHTML='<p>▾ 救护站选址</p><p>　main.py</p><p>　response_times.csv</p><p>　research_notes.md</p>';
      $('messages').innerHTML='<div class="guide-demo-message">你可以把选中的代码带入对话，一起检查覆盖条件。</div>';
    }
  }
  function focus(selector) {
    if(page==='solve'){
      document.body.classList.toggle('route-open',selector.includes('roadmap')||selector.includes('paper')||selector.includes('bg-toggle'));
      if(selector==='.wf-task-tabs')document.body.classList.add('workflow-panel-open');
    }
    if(page==='outline')document.body.classList.toggle('chapters-open',selector.includes('sectionsList'));
    if(page==='learn'){
      document.body.dataset.learnTab=selector==='#map'||selector==='.learn-controls'?'all':'path';
      if(number===17&&selector.includes('reader')&&$('reader').hidden)GuideKnowledge.reader(concept);
    }
    if(page==='assessment'&&number===18){if(selector==='#view-quiz'&&$('view-quiz').hidden)GuideAssessment.question();}
    if(page==='free')document.body.dataset.toolPanel=selector==='#explorer-tree'?'files':selector==='.ws-right'?'chat':'code';
  }
  try {
    document.body.classList.add('guide-preview-mode');
    document.addEventListener('click',e=>{
      const node=e.target.closest('[data-guide-action]');
      e.preventDefault();e.stopImmediatePropagation();
      if(node&&actions.has(node.dataset.guideAction)){actions.get(node.dataset.guideAction)();emit();}
    },true);
    document.addEventListener('submit',e=>e.preventDefault(),true);
    document.addEventListener('keydown',e=>{
      if(e.key==='Enter'&&!e.target.closest('[data-guide-action]')){e.preventDefault();e.stopImmediatePropagation();}
    },true);
    document.addEventListener('beforeinput',e=>{e.preventDefault();e.stopImmediatePropagation();},true);
    if(page==='home')home();else if(page==='solve')solve();else if(page==='outline')outline();else if(page==='paper')paper();else if(page==='learn')learn();else if(page==='assessment')assessment();else if(page==='practice')practice();else if(page==='free'||page==='ide')workspace();
    document.querySelectorAll('a').forEach(a=>{a.removeAttribute('href');a.removeAttribute('target');});
    document.querySelectorAll('textarea,input:not([type=checkbox]):not([type=radio])').forEach(n=>n.readOnly=true);
    window.GuidePreview={ready:true,focus,activate(name){if(actions.has(name)){actions.get(name)();emit();}}};
  } catch(error) {window.GuidePreview={ready:false,error:error.message};console.error('Guide preview:',error);}
})();
