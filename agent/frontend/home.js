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
      <div class="clog-ver">v2.1.0 · 2026-07-07</div>
      <div class="clog-body">
        <div class="clog-section">
          <div class="clog-h">🐛 修复：AI 回答被"吞掉"的严重 Bug</div>
          <p>流式回答有时会在眼前消失——深入排查 6 个独立根因并全部修复：</p>
          <ul>
            <li>SSE 缓冲区未 flush 导致最后的事件丢失</li>
            <li>网络错误时覆盖而非追加已有内容</li>
            <li>收尾阶段清空 DOM 重建导致闪白/丢失（核心修复）</li>
            <li>LLM 流中断时异常处理缺失</li>
            <li>渲染错误导致整个 SSE 流崩溃</li>
            <li>空 done 事件覆盖 token 增量渲染结果</li>
          </ul>
        </div>
        <div class="clog-section">
          <div class="clog-h">✨ 知识库按评级分层展示</div>
          <p>检索结果根据你的 L1~L5 评级自动调整：</p>
          <ul>
            <li><b>L1/L2 萌新/入门</b>：优先展示「一句话总结」+「举个例子」</li>
            <li><b>L4/L5 熟练/高手</b>：优先展示数学公式 + 推导 + 代码</li>
          </ul>
        </div>
        <div class="clog-section">
          <div class="clog-h">✨ 首页评级引导弹窗</div>
          <p>注册后若未评级，主页自动提醒去测评/选等级。</p>
        </div>
        <div class="clog-section">
          <div class="clog-h">🔧 改进</div>
          <ul>
            <li>角色/评级独立性澄清：难度深度 × 内容侧重 两个独立维度</li>
            <li>输入框侧重按钮 "跟随评级" → "自动"</li>
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
