/* 控制台：余额 / 卡密兑换 / API key 管理 / 用量。 */
(function () {
  const $ = (id) => document.getElementById(id);

  if (!G.token) {
    location.href = "/";
    return;
  }

  async function loadMe() {
    const r = await G.authedFetch("/api/me");
    if (!r.ok) return;
    const u = await r.json();
    $("who").textContent = u.nickname || u.phone;
  }

  const FREE_TOTAL = 1500000;

  async function loadBalance() {
    const r = await G.authedFetch("/api/balance");
    if (!r.ok) return;
    const b = await r.json();
    const left = Math.max(0, b.free_tokens_left || 0);
    const used = Math.max(0, FREE_TOTAL - left);
    const pct = Math.min(100, (used / FREE_TOTAL) * 100);

    $("balance").textContent = left.toLocaleString();
    $("token-total").textContent = FREE_TOTAL.toLocaleString();
    $("usage-fill").style.width = pct.toFixed(1) + "%";
    $("token-meta").textContent = left > 0
      ? `已用 ${used.toLocaleString()} token，剩余 ${left.toLocaleString()}`
      : "免费额度已用完";

    // 免费试用横幅
    const banner = $("free-banner");
    const bannerText = $("free-banner-text");
    if (left > 0) {
      banner.style.display = "";
      bannerText.textContent =
        `剩余免费额度 ${left.toLocaleString()} token` +
        (used > 0 ? `（已用 ${used.toLocaleString()}）` : "（全新账号，尽情使用吧！）");
    } else {
      banner.style.display = "none";
    }
  }

  async function loadKeys() {
    const r = await G.authedFetch("/api/keys");
    if (!r.ok) return;
    const { items } = await r.json();
    const tb = $("keys-body");
    tb.innerHTML = "";
    if (!items.length) {
      tb.innerHTML = `<tr><td colspan="5" style="color:var(--muted)">还没有 Key，点上方「生成新 Key」</td></tr>`;
      return;
    }
    for (const k of items) {
      const tr = document.createElement("tr");
      const status = k.revoked ? `<span class="tag revoked">已吊销</span>` : "";
      tr.innerHTML = `
        <td>${escapeHtml(k.name) || "—"} ${status}</td>
        <td class="mono">${escapeHtml(k.key_prefix)}…</td>
        <td>${G.fmtTime(k.created_at)}</td>
        <td>${G.fmtTime(k.last_used_at)}</td>
        <td></td>`;
      if (!k.revoked) {
        const btn = document.createElement("button");
        btn.className = "btn btn-danger btn-sm";
        btn.textContent = "吊销";
        btn.onclick = () => revokeKey(k.id);
        tr.lastElementChild.appendChild(btn);
      }
      tb.appendChild(tr);
    }
  }

  async function loadUsage() {
    const r = await G.authedFetch("/api/usage?limit=30");
    if (!r.ok) return;
    const { items } = await r.json();
    const tb = $("usage-body");
    tb.innerHTML = "";
    if (!items.length) {
      tb.innerHTML = `<tr><td colspan="4" style="color:var(--muted)">暂无调用记录</td></tr>`;
      return;
    }
    for (const u of items) {
      const tr = document.createElement("tr");
      const tok = (u.free_tokens_used && u.free_tokens_used > 0)
        ? u.free_tokens_used
        : (u.prompt_tokens || 0) + (u.completion_tokens || 0);
      tr.innerHTML = `
        <td>${G.fmtTime(u.created_at)}</td>
        <td class="mono">${escapeHtml(u.model)}</td>
        <td>${u.prompt_tokens} / ${u.completion_tokens}</td>
        <td>${tok.toLocaleString()} tok</td>`;
      tb.appendChild(tr);
    }
  }

  async function createKey() {
    $("create-key").disabled = true;
    try {
      const d = await G.postJSON("/api/keys", { name: $("key-name").value.trim() }, true);
      const box = $("new-key");
      box.style.display = "";
      box.innerHTML = `新 Key（请立即复制，仅显示这一次）：<br><b>${escapeHtml(d.key)}</b>`;
      $("key-name").value = "";
      await loadKeys();
    } catch (e) {
      alert(e.message);
    } finally {
      $("create-key").disabled = false;
    }
  }

  async function revokeKey(id) {
    if (!confirm("吊销后用这个 Key 的请求会立即失败，确定？")) return;
    const r = await G.authedFetch("/api/keys/" + encodeURIComponent(id), { method: "DELETE" });
    if (r.ok) loadKeys();
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  // 接入说明里展示网关地址 + 模型
  $("base-url").textContent = location.origin + "/v1";
  G.authedFetch("/api/models").then(async (r) => {
    if (r.ok) {
      const d = await r.json();
      $("models").textContent = (d.data || []).map((m) => m.id).join("、") || "—";
    }
  });

  $("create-key").onclick = createKey;
  $("logout").onclick = () => {
    G.clearToken();
    location.href = "/";
  };

  loadMe();
  loadBalance();
  loadKeys();
  loadUsage();
})();
