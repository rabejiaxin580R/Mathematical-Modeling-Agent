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
  }

  init();
})();
