/* 登录落地页：选头像 + 昵称 → 创建档案 → 跳 /home。
   若本机已有档案，提供「继续上次」入口。 */
(function () {
  const $ = (id) => document.getElementById(id);
  const AVATARS = ["fox", "panda", "owl", "cat", "rabbit", "penguin", "koala", "tiger"];
  let selected = "fox";

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

  async function enter() {
    const nickname = $("nickname").value.trim() || "建模新手";
    const btn = $("btn-enter");
    btn.disabled = true;
    btn.textContent = "创建中…";
    try {
      const r = await fetch("/api/profiles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ nickname, avatar: selected }),
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