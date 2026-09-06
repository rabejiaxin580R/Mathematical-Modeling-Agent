/* 模式选择主页：门禁 + 顶部用户信息 + 进度概览。 */
(function () {
  const $ = (id) => document.getElementById(id);

  async function init() {
    // 背景点阵网络母题（比 landing 更淡，不抢内容）+ 卡片级联入场
    if (window.MMAnim) {
      MMAnim.ambientNetwork("#bg-canvas", { density: 26000, inkAlpha: 0.08, dotAlpha: 0.3, interactive: false });
      MMAnim.reveal(document.querySelector(".home-cards"), { observe: false });
    }

    const p = await Profile.require();
    if (!p) return;

    $("greet").textContent = `${p.nickname}，选择一个开始的方式`;

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
    user.appendChild(roleBtn);

    user.appendChild(logout);

    // 进度概览
    const learned = (p.learn && p.learn.completed) ? p.learn.completed.length : 0;
    if (learned > 0) $("meta-learn").textContent = `已掌握 ${learned} 个知识点`;

    const attempts = (p.practice && p.practice.attempts) ? p.practice.attempts.length : 0;
    if (attempts > 0) $("meta-practice").textContent = `已练 ${attempts} 道真题`;

    // 新用户引导流程（登录后带 ?tour=1 参数，或从未完成过引导）
    const T = window.OnboardingTour;

    // 首次启动：若尚未配置 API 密钥，先弹「连接大模型」向导（两条路引导），
    // 配置完成（或用户选择稍后）再继续产品功能引导。
    const startTour = () => {
      if (!T) return;
      const params = new URLSearchParams(location.search);
      if (params.get("tour") === "1" || !T.isCompleted()) {
        if (params.get("tour")) {
          const url = new URL(location.href);
          url.searchParams.delete("tour");
          history.replaceState(null, "", url.toString());
        }
        setTimeout(() => { T.start(); }, 400);
      }
    };

    if (window.KeySetup) {
      KeySetup.ensure(startTour);
    } else {
      startTour();
    }

    if (T) {
      // 手动触发引导按钮
      const tourBtn = document.createElement("button");
      tourBtn.className = "home-logout";
      tourBtn.textContent = "使用引导";
      tourBtn.title = "重新查看产品引导";
      tourBtn.onclick = () => {
        T.reset();
        T.start();
      };
      user.appendChild(tourBtn);

      // 手动重新配置 API 密钥
      if (window.KeySetup) {
        const keyBtn = document.createElement("button");
        keyBtn.className = "home-logout";
        keyBtn.textContent = "API 设置";
        keyBtn.title = "重新配置大模型接口";
        keyBtn.onclick = () => KeySetup.open();
        user.appendChild(keyBtn);
      }
    }

    // 评级引导：未评测且未跳过的用户，弹窗引导去测评/选等级
    showAssessmentReminder(p);

    // 更新日志按钮
    bindChangelog();
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
