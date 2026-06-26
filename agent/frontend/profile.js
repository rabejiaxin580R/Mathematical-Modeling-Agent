/* 共享档案模块：本地档案门禁 + profile id 管理。
   所有需要登录的页面在最前面引入本文件。无密码，拿到 id 即视为该用户。 */
(function () {
  const KEY = "mm_profile_id";

  const Profile = {
    get id() {
      return localStorage.getItem(KEY) || "";
    },
    set(id) {
      if (id) localStorage.setItem(KEY, id);
    },
    clear() {
      localStorage.removeItem(KEY);
    },
    /** 拉取当前档案；失败（不存在/被删）返回 null。 */
    async fetch() {
      const id = this.id;
      if (!id) return null;
      try {
        const r = await fetch(`/api/profiles/${id}`);
        if (!r.ok) return null;
        return await r.json();
      } catch {
        return null;
      }
    },
    /** 门禁：无有效档案则跳登录页。返回档案对象。 */
    async require() {
      const p = await this.fetch();
      if (!p) {
        this.clear();
        location.href = "/";
        return null;
      }
      return p;
    },
    /** 头像 key → emoji。 */
    avatarEmoji(key) {
      const map = {
        fox: "🦊", panda: "🐼", owl: "🦉", cat: "🐱",
        rabbit: "🐰", penguin: "🐧", koala: "🐨", tiger: "🐯",
      };
      return map[key] || "🦊";
    },
  };

  window.Profile = Profile;
})();
