/* 登录 / 注册页逻辑：邮箱验证码二步注册。 */
(function () {
  const $ = (id) => document.getElementById(id);
  let isRegister = false;
  let codeSent = false;
  let countdownTimer = 0;

  function setMode(reg) {
    isRegister = reg;
    codeSent = false;
    clearInterval(countdownTimer);
    $("title").textContent = reg ? "注册" : "登录";
    $("submit").textContent = reg ? "发送验证码" : "登录";
    $("nickname-field").style.display = reg ? "" : "none";
    $("code-section").style.display = reg ? "" : "none";
    $("send-code").style.display = "none";
    $("vcode").style.display = "none";
    $("toggle-text").textContent = reg ? "已有账号？" : "还没有账号？";
    $("toggle").textContent = reg ? "去登录" : "注册一个";
    $("msg").textContent = "";
  }

  function showMsg(text, ok) {
    const m = $("msg");
    m.textContent = text;
    m.className = "msg " + (ok ? "ok" : "err");
  }

  function startCountdown(sec) {
    const btn = $("send-code");
    btn.disabled = true;
    function tick() {
      if (sec <= 0) {
        btn.disabled = false;
        btn.textContent = "重新发送";
        clearInterval(countdownTimer);
        return;
      }
      btn.textContent = sec + "s 后重发";
      sec--;
    }
    tick();
    countdownTimer = setInterval(tick, 1000);
  }

  async function requestCode() {
    const email = $("phone").value.trim();
    const password = $("password").value;
    if (!email || !password) {
      showMsg("请填写邮箱和密码");
      return;
    }
    if (password.length < 6) {
      showMsg("密码至少 6 位");
      return;
    }
    $("send-code").disabled = true;
    $("send-code").textContent = "发送中…";
    try {
      await G.postJSON("/api/register/request-code", {
        phone: email, password, nickname: $("nickname").value.trim(),
      });
      codeSent = true;
      showMsg("验证码已发送，请查收邮箱", true);
      $("submit").textContent = "完成注册";
      $("submit").disabled = false;
      // 显示验证码输入框，隐藏发送按钮
      $("send-code").style.display = "none";
      $("vcode").style.display = "";
      $("vcode").focus();
      startCountdown(60);
    } catch (e) {
      showMsg(e.message || "发送失败");
      $("send-code").disabled = false;
      $("send-code").textContent = "发送验证码";
    }
  }

  async function doLogin() {
    const email = $("phone").value.trim();
    const password = $("password").value;
    if (!email || !password) {
      showMsg("请填写邮箱和密码");
      return;
    }
    $("submit").disabled = true;
    try {
      const d = await G.postJSON("/api/login", { phone: email, password });
      G.setToken(d.token);
      location.href = "/dashboard";
    } catch (e) {
      showMsg(e.message || "登录失败");
      $("submit").disabled = false;
    }
  }

  async function doRegister() {
    const email = $("phone").value.trim();
    const password = $("password").value;
    if (!email || !password) {
      showMsg("请填写邮箱和密码");
      return;
    }
    if (password.length < 6) {
      showMsg("密码至少 6 位");
      return;
    }
    const code = $("vcode").value.trim();
    if (!code || code.length !== 6) {
      showMsg("请输入 6 位验证码");
      return;
    }
    $("submit").disabled = true;
    try {
      const d = await G.postJSON("/api/register/verify", { email, code });
      G.setToken(d.token);
      location.href = "/dashboard";
    } catch (e) {
      showMsg(e.message || "注册失败");
      $("submit").disabled = false;
    }
  }

  async function submit() {
    if (isRegister) {
      if (!codeSent) {
        await requestCode();
      } else {
        await doRegister();
      }
    } else {
      await doLogin();
    }
  }

  // 邮箱输入回车时触发发送验证码（注册模式）或登录
  $("password").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });
  $("vcode").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); submit(); }
  });

  $("submit").addEventListener("click", (e) => { e.preventDefault(); submit(); });
  $("send-code").addEventListener("click", (e) => { e.preventDefault(); requestCode(); });

  $("toggle").addEventListener("click", (e) => {
    e.preventDefault();
    setMode(!isRegister);
  });

  // 已登录直接进 dashboard
  if (G.token) {
    G.authedFetch("/api/me").then((r) => {
      if (r.ok) location.href = "/dashboard";
    });
  }
})();
