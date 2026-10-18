/* 模式选择主页：门禁 + 顶部用户信息 + 进度概览。 */
(function () {
  const $ = (id) => document.getElementById(id);

  async function init() {
    const p = await Profile.require();
    if (!p) return;

    $("greet").textContent = "继续研究";
    loadRecent();

    const user = $("home-user");
    user.innerHTML = "";
    const badge = document.createElement("span");
    badge.className = "home-avatar";
    badge.textContent = Profile.avatarEmoji(p.avatar);
    const name = document.createElement("span");
    name.className = "home-name";
    name.textContent = p.nickname;
    const logout = document.createElement("button");
    logout.className = "home-logout";
    logout.textContent = "切换档案";
    logout.onclick = () => {
      Profile.clear();
      location.href = "/";
    };
    user.appendChild(badge);
    user.appendChild(name);

    // 分工偏好：显示当前分工，点击可修改
    const roleBtn = document.createElement("button");
    roleBtn.className = "home-logout";
    roleBtn.title = "修改分工偏好，助教会据此调整回答侧重";
    const setRoleLabel = (role) => { roleBtn.textContent = "分工：" + Profile.roleLabel(role); };
    setRoleLabel(p.role || "");
    roleBtn.onclick = () => Profile.openRolePicker(p.role || "", (role) => { p.role = role; setRoleLabel(role); });
    $('home-options').appendChild(roleBtn);

    $('home-options').appendChild(logout);

    const T = window.OnboardingTour;
    if (T) {
      // 手动触发引导按钮
      // Shared navigation owns the per-block guide and saved progress.

      // 手动重新配置 API 密钥
      if (window.KeySetup) {
        const keyBtn = document.createElement("button");
        keyBtn.className = "home-logout";
        keyBtn.textContent = "API 设置";
        keyBtn.title = "重新配置大模型接口";
        keyBtn.onclick = () => KeySetup.open();
        $('home-options').appendChild(keyBtn);
      }
    }

    // 评级引导：未评测且未跳过的用户，弹窗引导去测评/选等级
    // Assessment stays available in the settings menu; avoid overlapping startup dialogs.

    // 更新日志按钮
    bindChangelog();
  }

  async function loadRecent() {
    const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const feature=$('home-feature'),recent=$('home-recent');
    feature.setAttribute('aria-busy','true');
    let visits={};
    try { visits=JSON.parse(localStorage.getItem('mm_research_visits_v1.'+(localStorage.getItem('mm_profile_id')||'guest'))||'{}') || {}; } catch {}
    const visitOf=item=>Number(visits[item.id]?.at)||0;
    const dateOf=(timestamp)=>new Date(timestamp).toLocaleDateString('zh-CN',{month:'long',day:'numeric'});
    const projectURL=item=>'/solve?session_id='+encodeURIComponent(item.id)+(visits[item.id]?.stepId?'&step_id='+encodeURIComponent(visits[item.id].stepId):'');
    try {
      const response=await fetch('/api/solve/sessions');if(!response.ok)throw Error('项目读取失败');
      const items=await response.json();
      items.sort((a,b)=>visitOf(b)-visitOf(a)||(b.updated_at||0)-(a.updated_at||0));
      recent.hidden=true;
      if(!items.length){$('greet').textContent='开始研究';feature.innerHTML='<div class="ui-empty"><h2>从一道题、一个想法开始。</h2><p>讨论思路，检验结果，留下自己的判断。</p><div class="home-actions"><a class="ui-btn primary" href="/solve">新建项目 <span aria-hidden="true">→</span></a><a class="home-secondary" href="/solve?example=1">查看示例</a></div></div>';return;}
      $('greet').textContent='继续研究';
      const latest=items[0],base='/solve?session_id='+encodeURIComponent(latest.id);
      const stamp=visitOf(latest),saved=Number(latest.updated_at)*1000;
      const meta=stamp?'上次访问 · '+dateOf(stamp):saved?'最近保存 · '+dateOf(saved):'当前项目';
      feature.innerHTML='<article class="ui-project-feature"><div class="home-project-copy"><p class="home-project-meta">'+esc(meta)+'</p><h2>'+esc(latest.title)+'</h2><p id="home-next" class="home-next">当前步骤 · '+esc(latest.current_title||'问题分析')+'</p></div><div class="home-actions"><a class="ui-btn primary" id="home-modeling" href="'+esc(projectURL(latest))+'">继续研究 <span aria-hidden="true">→</span></a><a class="home-secondary" href="/solve">新建项目</a></div></article>';
      if(items.length>1){
        const other=items.slice(1);let expanded=false;
        const renderRows=()=>{
          recent.hidden=false;
          recent.innerHTML='<div class="home-list-heading"><h2 class="ui-section-label">其他项目</h2>'+(other.length>3?'<button id="home-all-projects" class="home-secondary" type="button" aria-expanded="'+expanded+'" aria-controls="home-project-list">'+(expanded?'收起':'查看全部（'+other.length+'）')+'</button>':'')+'</div><div id="home-project-list">'+(expanded?other:other.slice(0,3)).map(item=>'<a class="ui-project-row" href="'+esc(projectURL(item))+'"><strong>'+esc(item.title)+'</strong><small>'+esc(visitOf(item)?'访问于 '+dateOf(visitOf(item)):item.updated_at?'保存于 '+dateOf(item.updated_at*1000):'已保存')+'</small><span aria-hidden="true">↗</span></a>').join('')+'</div>';
          if($('home-all-projects'))$('home-all-projects').onclick=()=>{expanded=!expanded;renderRows();$('home-all-projects').focus();};
        };renderRows();
      }
      // The main action is already usable while its next-step detail loads.
      feature.setAttribute('aria-busy','false');
      try {
        const r=await fetch('/api/solve/sessions/'+encodeURIComponent(latest.id));if(!r.ok)return;
        const d=await r.json(),steps=d.problem?.steps||[];
        const current=steps.find(s=>s.id===visits[latest.id]?.stepId)||steps[d.progress?.cur_step]||steps.find(s=>s.id===d.current_step)||steps[0];
        const review=steps.find(s=>d.outcomes?.[s.id]?.status==='needs_review');
        let target=base,next='当前步骤 · '+(current?.title||'问题分析');
        if(current)target+='&step_id='+encodeURIComponent(current.id);
        if(review){next+=' · '+(review.id===current?.id?'这一步需要复核':'有成果需要复核');if(review.id===current?.id)target+='&view=outcome';}
        else if(steps.length&&steps.every(s=>d.outcomes?.[s.id]?.status==='confirmed')){next='各步成果已确认，可以整理写作材料';target+='&view=outcome';}
        else if(current)next+=' · '+(d.outcomes?.[current.id]?.status==='draft'?'检查本步草稿与依据':'接着上次的思路继续');
        $('home-next').textContent=next;$('home-modeling').href=target;
      } catch { /* A detail failure must not hide the existing project or prevent opening it. */ }
    }catch(e){feature.innerHTML='<div class="ui-empty"><h2>暂时无法读取项目</h2><p>你的记录仍会保留，请重试。</p><div class="home-actions"><button id="home-retry" class="ui-btn">重新读取</button><a class="home-secondary" href="/solve">新建项目</a></div></div>';$('home-retry').onclick=loadRecent;}
    finally {feature.setAttribute('aria-busy','false');}
  }

  function bindChangelog() {
    const btn = document.getElementById("btn-changelog");
    if (!btn) return;
    btn.onclick = showChangelog;
  }

  function showChangelog() {
    // 防止重复弹窗
    if (document.querySelector(".clog-overlay")) return;

    const overlay = document.createElement("div");
    overlay.className = "clog-overlay";

    const card = document.createElement("div");
    card.className = "clog-card";
    card.innerHTML = `
      <div class="clog-close" title="关闭">×</div>
      <div class="clog-title">🆕 更新日志</div>
      <div class="clog-ver">v2.2.0 · 2026-09-06</div>
      <div class="clog-body">
        <div class="clog-section">
          <div class="clog-h">📝 新功能：一键生成建模论文</div>
          <p>从大纲到全文，自动配图 + 文献溯源：</p>
          <ul>
            <li><b>大纲 → 全文</b>：先确认大纲，再逐章生成，支持续传 / 重生成</li>
            <li><b>在线文献检索</b>：迁到网关服务端，鉴权不计费、结果缓存 7 天</li>
            <li><b>自动配图</b>：从数据表格自动选型成图（柱状 / 折线），直接注入正文</li>
            <li><b>导出 Word</b>：生成结果一键导出 .docx</li>
          </ul>
        </div>
        <div class="clog-section">
          <div class="clog-h">🎭 双风格等待界面</div>
          <p>剧情 / 终端两种日志风格可切换，章节进度条 + 心跳 + 卡住提示。</p>
        </div>
        <div class="clog-section">
          <div class="clog-h">🔧 改进</div>
          <ul>
            <li>「做建模」入口新增「论文生成」卡片</li>
            <li>完整论文页 UI 统一新中式配色</li>
            <li>文献检索兜底：线程化 + 20s 超时，不卡生成</li>
          </ul>
        </div>
      </div>
    `;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();
    card.querySelector(".clog-close").onclick = close;
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", esc); }
    });
  }

  async function showAssessmentReminder(p) {
    const asm = p.assessment || {};
    if (asm.level || asm.skipped) return;  // 已评级或已跳过

    // 创建弹窗
    const overlay = document.createElement("div");
    overlay.className = "asm-remind-overlay";

    const card = document.createElement("div");
    card.className = "asm-remind-card";
    card.innerHTML = `
      <div class="asm-remind-close" title="关闭（下次还会提醒）">×</div>
      <div class="asm-remind-title">你还没有评级哦</div>
      <div class="asm-remind-msg">完成入门测评，助教会根据你的水平调整回答深度与讲解方式。只需 5 分钟。</div>
      <div class="asm-remind-actions">
        <button class="asm-remind-btn primary">去测评 / 选等级</button>
        <button class="asm-remind-btn">稍后再说</button>
      </div>
    `;
    overlay.appendChild(card);
    document.body.appendChild(overlay);

    const close = () => overlay.remove();

    // 关闭按钮（不 skip，下次还提醒）
    card.querySelector(".asm-remind-close").onclick = close;
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });

    // 「去测评」
    card.querySelector(".asm-remind-btn.primary").onclick = () => {
      location.href = "/assessment";
    };

    // 「稍后再说」
    card.querySelector(".asm-remind-btn:not(.primary)").onclick = async () => {
      try { await fetch("/api/assessment/skip", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ pid: p.id }) }); } catch (_) {}
      close();
    };
  }

  init();
})();
