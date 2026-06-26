/* 客户端共享：登录 token 管理 + 带鉴权的 fetch。
 *
 * token 存 localStorage，请求经 Authorization: Bearer <token> 携带。
 */
(function () {
  const TOKEN_KEY = "gw_token";

  const G = {
    get token() {
      return localStorage.getItem(TOKEN_KEY) || "";
    },
    setToken(t) {
      if (t) localStorage.setItem(TOKEN_KEY, t);
    },
    clearToken() {
      localStorage.removeItem(TOKEN_KEY);
    },

    /** 带鉴权头的 fetch；401 时清 token 跳登录页。 */
    async authedFetch(url, opts = {}) {
      const headers = new Headers(opts.headers || {});
      if (this.token) headers.set("Authorization", "Bearer " + this.token);
      const resp = await fetch(url, { ...opts, headers });
      if (resp.status === 401) {
        this.clearToken();
        if (location.pathname !== "/") location.href = "/";
      }
      return resp;
    },

    async postJSON(url, body, authed) {
      const f = authed ? this.authedFetch.bind(this) : fetch;
      const r = await f(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body || {}),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || "请求失败");
      return d;
    },

    /** 分 → 元，保留两位。 */
    yuan(cents) {
      return (Math.round(cents) / 100).toFixed(2);
    },

    fmtTime(ts) {
      if (!ts) return "—";
      const d = new Date(ts * 1000);
      const p = (n) => String(n).padStart(2, "0");
      return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    },
  };

  window.G = G;
})();
