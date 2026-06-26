/* 登录 / 注册页逻辑。 */
(function () {
  const $ = (id) => document.getElementById(id);
  let isRegister = false;

  function setMode(reg) {
    isRegister = reg;
    $("title").textContent = reg ? "注册" : "登录";
    $("submit").textContent = reg ? "注册并登录" : "登录";
    $("nickname-field").style.display = reg ? "" : "none";
    $("toggle-text").textContent = reg ? "已有账号？" : "还没有账号？";
    $("toggle").textContent = reg ? "去登录" : "注册一个";
    $("msg").textContent = "";
  }

  function showMsg(text, ok) {
    const m = $("msg");
    m.textContent = text;
    m.className = "msg " + (ok ? "ok" : "err");
  }

  async function submit() {
    const phone = $("phone").value.trim();
    const password = $("password").value;
    if (!phone || !password) {
      showMsg("请填写账号和密码");
      return;
    }
    $("submit").disabled = true;
    try {
      let d;
      if (isRegister) {
        d = await G.postJSON("/api/register", {
          phone, password, nickname: $("nickname").value.trim(),
        });
      } else {
        d = await G.postJSON("/api/login", { phone, password });
      }
      G.setToken(d.token);
      location.href = "/dashboard";
    } catch (e) {
      showMsg(e.message || "失败");
    } finally {
      $("submit").disabled = false;
    }
  }

  // 已登录直接进 dashboard
  if (G.token) {
    G.authedFetch("/api/me").then((r) => {
      if (r.ok) location.href = "/dashboard";
    });
  }

  $("submit").addEventListener("click", (e) => { e.preventDefault(); submit(); });
  $("password").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });
  $("toggle").addEventListener("click", (e) => {
    e.preventDefault();
    setMode(!isRegister);
  });
})();
