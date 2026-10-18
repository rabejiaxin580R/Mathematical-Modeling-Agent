/* Explicit student actions on existing versioned research materials. */
(function(){
  'use strict';
  const enc=encodeURIComponent,esc=value=>MMRender.escapeHtml(String(value??''));
  const labels={student:'学生原话',ai:'AI 建议',unverified:'历史消息 · 来源待核对'};
  const drafts=new Map();
  function key(scope){return 'research.materials.v1.'+scope;}
  function read(scope){try{return drafts.get(scope)||JSON.parse(localStorage.getItem(key(scope))||'null');}catch{return drafts.get(scope);}}
  function stash(scope,value){drafts.set(scope,value);try{localStorage.setItem(key(scope),JSON.stringify(value));}catch{}}
  function clear(scope){drafts.delete(scope);try{localStorage.removeItem(key(scope));}catch{}}
  function element(tag,className,text){const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;}
  function command(text,fn,className='rm-link'){const node=element('button',className,text);node.type='button';node.onclick=fn;return node;}
  const base=sid=>'/api/solve/sessions/'+enc(sid);
  async function request(url,method='GET',body){
    const response=await fetch(url,{method,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
    const result=await response.json().catch(()=>({}));
    if(!response.ok){const error=new Error(typeof result.detail==='string'?result.detail:response.status===409?'已有新版本，你的文字已保留。请重新对照后保存。':'暂时没有连上，请重试。');error.status=response.status;throw error;}return result;
  }
  function dialog(title,kicker){
    const origin=document.activeElement,node=element('dialog','rm-dialog');
    node.setAttribute('aria-labelledby','rm-dialog-title');
    node.innerHTML='<header><div><p class="rm-kicker"></p><h2 id="rm-dialog-title"></h2></div><button type="button" class="rm-close" title="关闭" aria-label="关闭">'+(window.ResearchUI?.icon('close')||'×')+'</button></header><div class="rm-scroll"></div><p class="rm-feedback" role="status"></p>';
    node.querySelector('h2').textContent=title;node.querySelector('.rm-kicker').textContent=kicker;
    node.querySelector('.rm-close').onclick=()=>node.close();
    node.addEventListener('close',()=>{node.remove();if(origin?.isConnected&&origin.getClientRects().length)origin.focus({preventScroll:true});else document.getElementById('wf-status')?.focus({preventScroll:true});});
    document.body.append(node);node.showModal();node.querySelector('.rm-close').focus();
    return {node,body:node.querySelector('.rm-scroll'),say:text=>{node.querySelector('.rm-feedback').textContent=text;}};
  }
  function markdown(text){const node=element('div','panel-md rm-prose');MMRender.renderMarkdown(node,text||'尚无正文');return node;}
  function section(title){const node=element('section','rm-source-section');node.append(element('h3','',title));return node;}
  function heading(target,kind,context,refresh){
    if(document.documentElement.dataset.guidePreview)return;
    let head=target.querySelector('.rm-material-head');
    if(!head){head=element('div','rm-material-head');target.prepend(head);}
    const signature=[context.sessionId,context.stepId,context.stepTitle,kind].join(':');
    if(head.dataset.scope===signature)return;
    head.dataset.scope=signature;
    head.replaceChildren();const text=element('div');text.append(element('span','rm-kicker',context.stepTitle||'当前步骤'),element('h2','',kind==='note'?'研究笔记':'论文工作稿'));head.append(text);
    if(kind==='note')head.append(command('从讨论摘录',()=>capture({...context},refresh),'sv-btn rm-main-action'));
  }
  async function capture(context,refresh){
    const scope=context.sessionId+':'+context.stepId+':excerpt',d=dialog('留下这段思考',context.stepTitle||'研究笔记');
    const saved=read(scope);d.say('正在读取本步讨论…');
    try{
      const data=await request(base(context.sessionId)+'/research/messages?step_id='+enc(context.stepId));if(!d.node.open)return;
      const messages=data.messages||[];d.say('');
      if(!messages.length){d.body.append(element('p','rm-empty','还没有可摘录的完整讨论。回复完成后可以再来。'));return;}
      const form=element('form','rm-excerpt-form');form.innerHTML='<label>来自哪段讨论<select name="message" aria-label="摘录来源"></select></label><label>保留的原文<textarea name="quote" rows="4" maxlength="12000" required></textarea></label><label>我的理解<textarea name="idea" rows="3" maxlength="4000" required placeholder="这段话对我的模型意味着什么？"></textarea></label><label>还没想清楚的问题<textarea name="question" rows="2" maxlength="4000"></textarea></label><div class="rm-form-footer"><span data-origin></span><button type="submit" class="sv-btn rm-main-action">存入本步笔记</button></div>';
      const fields=form.elements;
      for(const message of messages){const option=element('option','',(labels[message.attribution]||'讨论')+' · '+message.content.replace(/\s+/g,' ').slice(0,82));option.value=message.id;fields.message.append(option);}
      fields.message.value=messages.some(m=>m.id===saved?.message_id)?saved.message_id:messages.filter(m=>m.role==='user').at(-1)?.id||messages.at(-1).id;
      let operation=saved?.operation_id||crypto.randomUUID(),submitted=saved?.submitted||null;
      const source=()=>messages.find(m=>m.id===fields.message.value);
      const remember=()=>stash(scope,{message_id:fields.message.value,quote:fields.quote.value,idea:fields.idea.value,question:fields.question.value,operation_id:operation,submitted});
      const setSource=()=>{fields.quote.value=source().content.slice(0,12000);form.querySelector('[data-origin]').textContent=labels[source().attribution];};
      setSource();if(saved){fields.idea.value=saved.idea||'';fields.question.value=saved.question||'';if(saved.message_id===fields.message.value)fields.quote.value=saved.quote||'';}
      fields.message.onchange=()=>{setSource();remember();};form.oninput=remember;
      form.onsubmit=async event=>{
        event.preventDefault();const controls=[...form.querySelectorAll('input,textarea,select,button')];controls.forEach(node=>node.disabled=true);
        const value={step_id:context.stepId,message_id:fields.message.value,quote:fields.quote.value,idea:fields.idea.value,question:fields.question.value};
        const serialized=JSON.stringify(value);if(submitted&&submitted!==serialized)operation=crypto.randomUUID();submitted=serialized;remember();
        try{await request(base(context.sessionId)+'/research/notes','POST',{...value,operation_id:operation});clear(scope);await refresh();if(d.node.open)d.node.close();}
        catch(error){d.say(error.message);}finally{controls.forEach(node=>node.disabled=false);}
      };
      d.body.append(form);
    }catch(error){d.say(error.message);d.body.append(command('重新读取',()=>{d.node.close();capture(context,refresh);}));}
  }
  async function sources(context,artifact){
    const d=dialog('这份材料的依据',artifact.title||'研究出处');d.say('正在核对来源…');
    try{
      const data=await request(base(context.sessionId)+'/research/artifacts/'+enc(artifact.id)+'/sources');if(!d.node.open)return;d.say('');
      if(data.missing?.length){const notice=section('尚待核对');notice.append(element('p','rm-caution',data.missing.join('；')));d.body.append(notice);}
      const original=section('讨论原文');
      for(const m of data.messages){const item=element('article','rm-source-item');item.append(element('span','rm-kicker',labels[m.attribution]||'讨论原文'),element('p','rm-original',m.content));original.append(item);}
      if(!data.messages.length)original.append(element('p','rm-empty','这份材料没有关联可核对的讨论消息。'));d.body.append(original);
      if(artifact.kind==='paper'){
        const notes=section('采用的笔记版本');for(const note of data.notes){notes.append(element('h4','',note.title+' · 第 '+note.revision+' 版'),markdown(note.content));}
        if(!data.notes.length)notes.append(element('p','rm-empty',data.note_provenance_available?'没有关联笔记。':'这份历史稿未记录笔记版本，不能据此推断采用了哪一版。'));d.body.append(notes);
      }
      const evidence=section('计算与验证');for(const record of data.evidence){const item=element('article','rm-source-item');item.append(element('h4','',record.purpose||'验证记录'),element('p','rm-kicker',runLabel(record)+' · '+codeLabel(record)),element('pre','rm-output',record.stdout||record.stderr||'无文字输出'));evidence.append(item);}
      if(!data.evidence.length)evidence.append(element('p','rm-empty','没有关联验证记录。'));d.body.append(evidence);
      if(artifact.kind==='paper'){
        const citations=section('文献题录与摘要');
        for(const citation of data.citations){const item=element('article','rm-citation');item.append(element('h4','',citation.title),element('p','rm-kicker',(citation.authors||[]).map(a=>typeof a==='string'?a:a.name||'').join('、')+' · '+(citation.published||'年份未提供')));
          item.append(element('p','rm-original',citation.abstract||'只有题录，尚无摘要。'),element('p','rm-caution',citation.source_kind==='metadata'?'仅已核对题录，尚未读取摘要或全文。':citation.source_kind==='fulltext'?'来源标注为全文，请核对实际引文。':'检索摘要，尚未读取全文；此处列出材料来源，不代表正文已完成引用。'));
          if(/^https?:\/\//.test(citation.url)){const link=element('a','rm-link','打开文献原页');link.href=citation.url;link.target='_blank';link.rel='noopener noreferrer';item.append(link);}citations.append(item);
        }
        if(!data.citations.length)citations.append(element('p','rm-empty','没有可核对的文献题录。'));d.body.append(citations);
      }
      if(data.warnings?.length){const warnings=section('待补充');warnings.append(element('p','rm-caution',data.warnings.join('；')));d.body.append(warnings);}
    }catch(error){d.say(error.message);d.body.append(command('重试读取',()=>{d.node.close();sources(context,artifact);}));}
  }
  function materialActions(node,artifact,context,options){
    const actions=node.querySelector('.wf-material-actions');if(!actions)return;
    actions.prepend(command('查看依据',()=>sources({...context},artifact)));
    if(artifact.kind==='paper'&&artifact.suggested_for&&artifact.status!=='adopted')actions.prepend(command('对照并采纳',()=>compare({...context},artifact,options),'sv-btn rm-main-action'));
    node.dataset.materialKind=artifact.kind;node.dataset.suggestion=String(!!artifact.suggested_for);
  }
  async function compare(context,suggestion,options){
    const target=options.artifacts().find(a=>a.id===suggestion.suggested_for);
    if(!target){dialog('原稿暂时不可用',suggestion.title||'论文修改建议').say('这份建议的原稿暂时无法找到，建议原文仍保留。');return;}
    const d=dialog('保留你的稿，再决定怎样改',suggestion.title||'论文修改建议');d.node.classList.add('rm-compare-dialog');
    const scope=context.sessionId+':'+suggestion.id+':merge',saved=read(scope);
    const local=options.draft(target.id),openingDraft=local?.content;
    let current=target,proposal=suggestion,operation=saved?.operation_id||crypto.randomUUID(),submitted=saved?.submitted||'';
    const columns=element('div','rm-compare-columns'),left=section('当前稿 · 第 '+target.revision+' 版'),right=section('AI 修改建议 · 第 '+suggestion.revision+' 版');
    left.append(markdown(target.content));right.append(markdown(suggestion.content));columns.append(left,right);d.body.append(columns);
    const form=element('form','rm-merge-form');form.innerHTML='<div class="rm-section-heading"><label for="rm-merged-text">准备写回的正文</label><button type="button" class="rm-link" data-use-suggestion>使用建议全文</button></div><textarea id="rm-merged-text" rows="7" required></textarea><div class="rm-form-footer"><span>保存为新版本，原稿仍在历史中。</span><button type="submit" class="sv-btn rm-main-action">保存我核对后的版本</button></div>';
    const input=form.querySelector('textarea');input.value=saved?.content??local?.content??target.content;
    const remember=()=>stash(scope,{content:input.value,operation_id:operation,submitted,base_revision:current.revision});input.oninput=remember;
    form.querySelector('[data-use-suggestion]').onclick=()=>{input.value=proposal.content;remember();input.focus();};
    if(local)d.say('已带入你在当前浏览器尚未保存的编辑。');
    if(saved?.base_revision&&saved.base_revision!==target.revision)d.say('原稿已有更新，你的合并草稿保留在下方，请对照当前稿。');
    const reload=command('重新读取版本',async()=>{reload.disabled=true;try{const latest=await request(base(context.sessionId)+'/workflow');current=latest.artifacts.find(a=>a.id===target.id);proposal=latest.artifacts.find(a=>a.id===suggestion.id);if(!current||!proposal)throw new Error('材料已不可用，请返回项目。');left.replaceChildren(element('h3','','当前稿 · 第 '+current.revision+' 版'),markdown(current.content));right.replaceChildren(element('h3','','AI 修改建议 · 第 '+proposal.revision+' 版'),markdown(proposal.content));reload.hidden=true;d.say('已载入最新版本，你的合并文字未改动。请核对后保存。');}catch(error){d.say(error.message);}finally{reload.disabled=false;}});reload.hidden=true;
    form.onsubmit=async event=>{event.preventDefault();const controls=[...form.querySelectorAll('textarea,button')];controls.forEach(node=>node.disabled=true);
      const payload={expected_revision:proposal.revision,expected_target_revision:current.revision,content:input.value};
      const serialized=JSON.stringify(payload);if(submitted&&submitted!==serialized)operation=crypto.randomUUID();submitted=serialized;remember();
      try{const result=await request(base(context.sessionId)+'/research/artifacts/'+enc(suggestion.id)+'/adopt','POST',{...payload,operation_id:operation});clear(scope);await options.adopted(target.id,openingDraft,result.artifact);if(d.node.open)d.node.close();}
      catch(error){d.say(error.message);reload.hidden=error.status!==409;}finally{controls.forEach(node=>node.disabled=false);}
    };form.append(reload);d.body.append(form);
  }
  function runLabel(record){return record.stale?'研究条件已变化 · 需复核':record.status==='cancelled'?'已取消':record.status==='failed'?'运行未完成':record.status==='success'?'程序运行完成 · 结论待判断':'外部依据';}
  function codeLabel(record){return record.code_revision?'代码第 '+record.code_revision+' 版':'未记录代码版本';}
  function display(value){return typeof value==='object'?JSON.stringify(value):String(value??'未报告');}
  function metricsTable(records){
    const keys=[...new Set(records.flatMap(r=>Object.keys(r.metrics||{})))].slice(0,24),table=element('table','rm-metrics');
    const head=element('thead');head.innerHTML='<tr><th scope="col">输出项</th>'+records.map((r,i)=>'<th scope="col">'+(records.length>1?'运行 '+(i+1):'结果')+'</th>').join('')+'</tr>';table.append(head);
    const body=element('tbody');for(const name of keys){const row=element('tr');row.append(element('th','',name));row.firstChild.scope='row';for(const r of records)row.append(element('td','',Object.hasOwn(r.metrics||{},name)?display(r.metrics[name]):'未报告'));body.append(row);}table.append(body);return table;
  }
  function evidence(wrap,records,context){
    if(document.documentElement.dataset.guidePreview)return;
    const rows=[...records].reverse();
    for(const record of rows){const node=wrap.querySelector('[data-record="'+CSS.escape(record.id)+'"]');if(!node)continue;
      node.classList.add('rm-experiment');
      const label=node.querySelector('.flow-note');if(label)label.textContent=runLabel(record)+' · '+codeLabel(record)+' · '+new Date(record.created_at*1000).toLocaleString('zh-CN');
      if(record.stale)node.dataset.stale='true';
      const raw=node.querySelector(':scope > pre');if(raw&&Object.keys(record.metrics||{}).length){raw.hidden=true;raw.after(metricsTable([record]));}
      const action=command('核对本次输入与代码',()=>{
        const d=dialog(record.purpose||'本次验证','归档快照 · '+codeLabel(record)),code=section('这次实际运行的代码');code.append(element('pre','rm-output',record.code||'未记录代码'));d.body.append(code);
        const inputs=section('归档输入');if(!record.inputs?.length)inputs.append(element('p','rm-empty','没有记录外部输入文件；输入也可能直接写在代码中。'));
        for(const input of record.inputs||[]){inputs.append(element('p','rm-original',input.name+(input.unavailable?' · '+input.unavailable:'')),element('code','rm-hash',input.sha256||'未记录校验值'));}
        d.body.append(inputs);const output=section('原始输出');output.append(element('pre','rm-output',record.stdout||record.stderr||'无文字输出'));d.body.append(output);
      });node.append(action);
    }
    if(rows.length>1){const button=command('对比两次运行',()=>compareRuns(rows), 'sv-btn rm-main-action');button.id='rm-compare-runs';wrap.firstElementChild.querySelector('h3')?.after(button);}
  }
  function compareRuns(records){
    const d=dialog('两次运行，放在一起看','计算验证');d.node.classList.add('rm-compare-dialog');
    const selectors=element('div','rm-run-selectors');
    const selects=[0,1].map((_,index)=>{const label=element('label','','运行 '+(index+1)),select=element('select');select.setAttribute('aria-label','对比运行 '+(index+1));for(const record of records){const option=element('option','',(record.purpose||'验证')+' · '+new Date(record.created_at*1000).toLocaleTimeString('zh-CN'));option.value=record.id;select.append(option);}select.value=records[index].id;label.append(select);selectors.append(label);return select;});
    const content=element('div');d.body.append(selectors,content);
    const render=()=>{const pair=selects.map(select=>records.find(r=>r.id===select.value));content.replaceChildren();
      if(pair[0].id===pair[1].id){content.append(element('p','rm-caution','请选择两条不同的运行记录。'));return;}
      content.append(element('p','rm-caution',pair.every(r=>r.inputs?.length&&r.input_hash)?pair[0].input_hash===pair[1].input_hash?'归档文件校验值一致；仍需核对代码中的模型与参数。':'两次归档文件不同，结果差异不能只归因于代码。':pair.every(r=>!r.inputs?.length)?'未归档外部输入，参数可能写在代码中，需对照本次代码。':'输入校验值不完整，尚不能判断两次输入是否相同。'));
      const summary=element('div','rm-run-summary');pair.forEach((r,i)=>{const item=section('运行 '+(i+1));item.append(element('p','',r.purpose),element('p','rm-kicker',runLabel(r)+' · '+codeLabel(r)));summary.append(item);});content.append(summary);
      if(pair.some(r=>Object.keys(r.metrics||{}).length))content.append(metricsTable(pair));
      const logs=element('div','rm-compare-columns');pair.forEach((r,i)=>{const sectionNode=section('运行 '+(i+1)+' · 原始输出');sectionNode.append(element('pre','rm-output',r.stdout||r.stderr||'无文字输出'));const code=element('details');code.append(element('summary','','本次归档代码'),element('pre','rm-output',r.code||'未记录代码'));sectionNode.append(code);logs.append(sectionNode);});content.append(logs);
    };selects.forEach(select=>select.onchange=render);render();
  }
  window.ResearchMaterials={heading,materialActions,evidence};
})();
