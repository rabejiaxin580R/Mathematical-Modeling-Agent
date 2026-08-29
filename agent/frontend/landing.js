/* 登录落地页：选头像 + 昵称 → 创建档案 → 跳 /home。
   若本机已有档案，提供「继续上次」入口。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const AVATARS = ["fox", "panda", "owl", "cat", "rabbit", "penguin", "koala", "tiger"];
  let selected = "fox";

  // 分工偏好：影响助教回答侧重。"" = 无偏好（按能力评级自适应）
  const ROLES = [
    { key: "", icon: "🎓", label: "无偏好", desc: "按评级自适应" },
    { key: "coding", icon: "💻", label: "编程手", desc: "多代码与实现" },
    { key: "writing", icon: "✍️", label: "写作手", desc: "多论文与表述" },
    { key: "modeling", icon: "📐", label: "建模手", desc: "多模型与公式" },
  ];
  let selectedRole = "";

  function renderAvatars() {
    const grid = $("avatar-grid");
    grid.innerHTML = "";
    for (const key of AVATARS) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "avatar-opt" + (key === selected ? " sel anim-pop" : "");
      b.textContent = Profile.avatarEmoji(key);
      b.title = key;
      b.onclick = () => {
        selected = key;
        renderAvatars();
      };
      grid.appendChild(b);
    }
  }

  function renderRoles() {
    const grid = $("role-grid");
    if (!grid) return;
    grid.innerHTML = "";
    for (const r of ROLES) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "role-opt" + (r.key === selectedRole ? " sel anim-pop" : "");
      b.innerHTML = `<span class="role-icon">${r.icon}</span>` +
        `<span class="role-label">${r.label}</span>` +
        `<span class="role-desc">${r.desc}</span>`;
      b.onclick = () => { selectedRole = r.key; renderRoles(); };
      grid.appendChild(b);
    }
  }

  async function enter() {
    const nickname = $("nickname").value.trim() || "建模新手";
    const btn = $("btn-enter");
    btn.disabled = true;
    btn.textContent = "创建中…";
    try {
      const r = await fetch("/api/profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname, avatar: selected, role: selectedRole }),
      });
      if (!r.ok) throw new Error("创建失败");
      const profile = await r.json();
      Profile.set(profile.id);
      // 新用户：跳转主页并触发引导流程
      location.href = "/home?tour=1";
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "进入";
      alert("创建档案失败，请重试。");
    }
  }

  async function showExisting() {
    const p = await Profile.fetch();
    if (!p) return;
    const box = $("existing");
    box.hidden = false;
    box.innerHTML = "";
    const span = document.createElement("span");
    span.textContent = `检测到本机档案：${Profile.avatarEmoji(p.avatar)} ${p.nickname}`;
    const cont = document.createElement("button");
    cont.className = "landing-link";
    cont.textContent = "继续上次 →";
    cont.onclick = () => (location.href = "/home");
    box.appendChild(span);
    box.appendChild(cont);
  }

  renderAvatars();
  renderRoles();
  $("btn-enter").onclick = enter;
  $("nickname").addEventListener("keydown", (e) => {
    if (e.key === "Enter") enter();
  });
  showExisting();
})();

// ===== 登录页动态几何背景：复用 MMAnim 点阵连线网络（含鼠标互动） =====
(function () {
  if (window.MMAnim) {
    MMAnim.ambientNetwork("#bg-canvas", { density: 15000, inkAlpha: 0.16, dotAlpha: 0.5, interactive: true });
  }
})();