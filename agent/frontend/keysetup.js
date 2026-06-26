// ===== 首次启动 · API 密钥配置向导 =====
// 目标：让没有任何技术背景的新手，在第一次打开 app 时就清楚地知道
// 「两种方式」都能用 —— ① 用我们提供的额度（去网关注册领 Key），
// ② 用自己的 API Key（DeepSeek / 通义 / OpenAI / Moonshot / 自定义）。
//
// 它是一个独立模块，被需要的页面引入；只在「尚未配置密钥」时自动弹出，
// 也可由设置入口手动唤起（KeySetup.open()）。保存复用既有 /api/settings。

const KeySetup = (() => {
  const PRESETS = {
    deepseek: { name: "DeepSeek", base_url: "https://api.deepseek.com/v1", model: "deepseek-chat", get: "https://platform.deepseek.com/api_keys" },
    qwen:     { name: "通义千问", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", model: "qwen-plus", get: "https://bailian.console.aliyun.com/?apiKey=1" },
    openai:   { name: "OpenAI", base_url: "https://api.openai.com/v1", model: "gpt-4o-mini", get: "https://platform.openai.com/api-keys" },
    moonshot: { name: "Moonshot", base_url: "https://api.moonshot.cn/v1", model: "moonshot-v1-8k", get: "https://platform.moonshot.cn/console/api-keys" },
    custom:   { name: "自定义", base_url: "", model: "", get: "" },
  };

  let overlay = null;
  let info = null;       // /api/onboarding 返回
  let onDone = null;
  let provider = "deepseek";

  async function loadInfo() {
    try {
      info = await (await fetch("/api/onboarding")).json();
    } catch {
      info = { has_api_key: false, gateway: { enabled: false } };
    }
    return info;
  }

  // ---------- 入口：尚未配置密钥才弹 ----------
  async function ensure(callback) {
    onDone = callback || null;
    await loadInfo();
    if (info.has_api_key) { if (onDone) onDone(); return; }
    render();
  }

  // ---------- 手动打开（设置里「重新配置」用） ----------
  async function open(callback) {
    onDone = callback || null;
    await loadInfo();
    render();
  }

  // ---------- 构建浮层 ----------
  function render() {
    if (overlay) destroy();
    const gw = (info && info.gateway) || { enabled: false };

    overlay = document.createElement("div");
    overlay.className = "ks-overlay";
    overlay.innerHTML = `
      <div class="ks-backdrop"></div>
      <div class="ks-card" role="dialog" aria-modal="true">
        <button class="ks-x" id="ks-x" title="稍后配置">×</button>
        <div class="ks-head">
          <span class="ks-mark">绣</span>
          <div>
            <h2 class="ks-title">连接 AI 大模型</h2>
            <p class="ks-sub">聊天、跑代码都需要一个大模型接口。两种方式任选其一，随时可在「设置」里更换。</p>
          </div>
        </div>

        <div class="ks-choices" id="ks-choices">
          <button class="ks-choice ${gw.enabled ? "" : "ks-disabled"}" data-go="gateway" ${gw.enabled ? "" : "disabled"}>
            <div class="ks-choice-ic">🎁</div>
            <h3>用我们提供的额度</h3>
            <p>${gw.enabled ? "去注册站点领取额度、生成一个 Key，粘回来即可。新手最省事。" : "（管理员未配置内置网关，暂不可用）"}</p>
            <span class="ks-tag">${gw.enabled ? "推荐新手" : "未开启"}</span>
          </button>

          <button class="ks-choice" data-go="byok">
            <div class="ks-choice-ic">🔑</div>
            <h3>用我自己的 API Key</h3>
            <p>已有 DeepSeek / 通义 / OpenAI / Moonshot 等账号？填入你自己的 Key 直接用。</p>
            <span class="ks-tag">已有账号</span>
          </button>
        </div>

        <div class="ks-panel" id="ks-panel" hidden></div>

        <div class="ks-foot">
          <button class="ks-later" id="ks-later">稍后配置</button>
          <span class="ks-status" id="ks-status"></span>
        </div>
      </div>`;
    document.body.appendChild(overlay);

    overlay.querySelector("#ks-x").onclick = later;
    overlay.querySelector("#ks-later").onclick = later;
    overlay.querySelectorAll(".ks-choice[data-go]").forEach((b) => {
      b.onclick = () => { if (!b.disabled) showPanel(b.dataset.go); };
    });

    requestAnimationFrame(() => overlay.classList.add("ks-in"));
  }

  // ---------- 两条路各自的面板 ----------
  function showPanel(go) {
    const panel = overlay.querySelector("#ks-panel");
    overlay.querySelector("#ks-choices").classList.add("ks-collapsed");
    panel.hidden = false;
    if (go === "gateway") panelGateway(panel);
    else panelByok(panel);
    panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  function backRow() {
    return `<button class="ks-back" id="ks-back">← 换一种方式</button>`;
  }
  function bindBack() {
    const b = overlay.querySelector("#ks-back");
    if (b) b.onclick = () => {
      overlay.querySelector("#ks-choices").classList.remove("ks-collapsed");
      overlay.querySelector("#ks-panel").hidden = true;
    };
  }

  // 路 ①：内置网关
  function panelGateway(panel) {
    const gw = info.gateway;
    panel.innerHTML = `
      ${backRow()}
      <ol class="ks-steps">
        <li>点下方按钮打开注册站点，注册账号（手机号即可），领取或兑换额度。</li>
        <li>在站点的「API Key」页生成一个 Key（形如 <code>sk-…</code>），复制它。</li>
        <li>回到这里，把 Key 粘到下面，点「保存并开始」。</li>
      </ol>
      <a class="ks-open-site" href="${gw.signup_url}" target="_blank" rel="noopener">🌐 打开注册站点领取额度</a>
      <label class="ks-label">把生成的 API Key 粘到这里</label>
      <input class="ks-input" id="ks-gw-key" type="text" placeholder="sk-xxxxxxxxxxxxxxxx" autocomplete="off" />
      <p class="ks-mini">接口地址将自动设为 <code>${gw.base_url}</code>，模型 <code>${gw.model}</code>。</p>
      <button class="ks-save" id="ks-gw-save">保存并开始 →</button>`;
    bindBack();
    overlay.querySelector("#ks-gw-save").onclick = () => {
      const key = overlay.querySelector("#ks-gw-key").value.trim();
      if (!key) return setStatus("请先粘贴 API Key", "err");
      save({ api_key: key, base_url: gw.base_url, model: gw.model });
    };
  }

  // 路 ②：自带 Key
  function panelByok(panel) {
    const buttons = Object.entries(PRESETS)
      .map(([k, p]) => `<button class="ks-prov ${k === provider ? "active" : ""}" data-prov="${k}">${p.name}</button>`)
      .join("");
    panel.innerHTML = `
      ${backRow()}
      <label class="ks-label">选择服务商</label>
      <div class="ks-provs" id="ks-provs">${buttons}</div>
      <a class="ks-getkey" id="ks-getkey" href="#" target="_blank" rel="noopener" hidden>↗ 去这家官网申请 API Key</a>
      <label class="ks-label">API 密钥</label>
      <input class="ks-input" id="ks-bk-key" type="text" placeholder="sk-xxxxxxxxxxxxxxxx" autocomplete="off" />
      <label class="ks-label">接口地址 (Base URL)</label>
      <input class="ks-input" id="ks-bk-url" type="text" placeholder="https://api.deepseek.com/v1" />
      <label class="ks-label">模型名称</label>
      <input class="ks-input" id="ks-bk-model" type="text" placeholder="deepseek-chat" />
      <button class="ks-save" id="ks-bk-save">保存并开始 →</button>`;
    bindBack();
    applyProvider(provider);
    panel.querySelectorAll(".ks-prov").forEach((b) => {
      b.onclick = () => { provider = b.dataset.prov; applyProvider(provider); };
    });
    overlay.querySelector("#ks-bk-save").onclick = () => {
      const key = overlay.querySelector("#ks-bk-key").value.trim();
      const url = overlay.querySelector("#ks-bk-url").value.trim();
      const model = overlay.querySelector("#ks-bk-model").value.trim();
      if (!key) return setStatus("请填入你的 API 密钥", "err");
      if (!url) return setStatus("请填入接口地址 Base URL", "err");
      if (!model) return setStatus("请填入模型名称", "err");
      save({ api_key: key, base_url: url, model });
    };
  }

  function applyProvider(prov) {
    overlay.querySelectorAll(".ks-prov").forEach((b) =>
      b.classList.toggle("active", b.dataset.prov === prov));
    const p = PRESETS[prov];
    if (prov !== "custom") {
      overlay.querySelector("#ks-bk-url").value = p.base_url;
      overlay.querySelector("#ks-bk-model").value = p.model;
    }
    const link = overlay.querySelector("#ks-getkey");
    if (p.get) { link.href = p.get; link.hidden = false; link.textContent = `↗ 去 ${p.name} 官网申请 API Key`; }
    else link.hidden = true;
  }

  // ---------- 保存 ----------
  async function save(payload) {
    setStatus("保存中…", "");
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!r.ok) {
        const e = await r.json().catch(() => ({}));
        throw new Error(e.detail || r.statusText);
      }
      setStatus("已连接，正在进入…", "ok");
      setTimeout(() => { destroy(); if (onDone) onDone(); }, 900);
    } catch (e) {
      setStatus("保存失败：" + e.message, "err");
    }
  }

  function setStatus(text, kind) {
    const el = overlay && overlay.querySelector("#ks-status");
    if (!el) return;
    el.textContent = text;
    el.className = "ks-status" + (kind ? " " + kind : "");
  }

  function later() {
    destroy();
    if (onDone) onDone();
  }

  function destroy() {
    if (!overlay) return;
    overlay.classList.add("ks-out");
    const el = overlay;
    overlay = null;
    setTimeout(() => el.parentNode && el.parentNode.removeChild(el), 280);
  }

  return { ensure, open };
})();

window.KeySetup = KeySetup;
