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
    async fetch({strict = false} = {}) {
      const id = this.id;
      if (!id) return null;
      try {
        const r = await fetch(`/api/profiles/${id}`);
        if (r.status === 404) return null;
        if (!r.ok) throw new Error('档案暂时无法读取');
        return await r.json();
      } catch (error) {
        if (strict) throw error;
        return null;
      }
    },
    /** 门禁：无有效档案则跳登录页。返回档案对象。 */
    async require() {
      let p;
      try { p = await this.fetch({strict:true}); }
      catch {
        if (!document.getElementById('profile-load-error')) {
          const notice = document.createElement('section');
          notice.id = 'profile-load-error'; notice.setAttribute('role', 'alert');
          notice.style.cssText = 'position:fixed;top:16px;left:50%;transform:translateX(-50%);z-index:9999;background:white;color:#20242b;padding:16px;border:1px solid #dde1e7;border-radius:8px;max-width:90vw';
          const message = document.createElement('p');
          message.textContent = '暂时无法读取档案，你的档案和导览进度已保留。';
          const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = '重新读取';
          retry.onclick = () => location.reload();
          notice.append(message, retry); document.body.append(notice);
        }
        return null;
      }
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
    /** 分工 key → 显示名。 */
    roleLabel(key) {
      const map = { coding: "编程手", writing: "写作手", modeling: "建模手" };
      return map[key] || "无偏好";
    },
    /** 更新分工偏好，返回更新后的档案（失败返回 null）。 */
    async updateRole(role) {
      const id = this.id;
      if (!id) return null;
      try {
        const r = await fetch(`/api/profiles/${id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role }),
        });
        if (!r.ok) return null;
        return await r.json();
      } catch {
        return null;
      }
    },
    /** 弹出分工选择浮层。current=当前分工；onSaved(role) 保存成功后回调。 */
    openRolePicker(current, onSaved) {
      const ROLES = [
        { key: "", icon: "🎓", label: "无偏好", desc: "按能力评级自适应" },
        { key: "coding", icon: "💻", label: "编程手", desc: "多代码与实现细节" },
        { key: "writing", icon: "✍️", label: "写作手", desc: "多论文结构与表述" },
        { key: "modeling", icon: "📐", label: "建模手", desc: "多模型与公式推导" },
      ];
      let sel = current || "";
      const overlay = document.createElement("div");
      overlay.className = "role-modal-overlay";
      overlay.innerHTML =
        `<div class="role-modal">
           <div class="role-modal-title">选择你的分工</div>
           <div class="role-modal-sub">助教会据此、并结合你的能力评级调整回答侧重。</div>
           <div class="role-modal-grid"></div>
           <div class="role-modal-actions">
             <button class="role-modal-cancel">取消</button>
             <button class="role-modal-save">保存</button>
           </div>
         </div>`;
      const grid = overlay.querySelector(".role-modal-grid");
      const draw = () => {
        grid.innerHTML = "";
        for (const r of ROLES) {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "role-opt" + (r.key === sel ? " sel" : "");
          b.innerHTML = `<span class="role-icon">${r.icon}</span>` +
            `<span class="role-label">${r.label}</span>` +
            `<span class="role-desc">${r.desc}</span>`;
          b.onclick = () => { sel = r.key; draw(); };
          grid.appendChild(b);
        }
      };
      draw();
      const close = () => overlay.remove();
      overlay.querySelector(".role-modal-cancel").onclick = close;
      overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
      overlay.querySelector(".role-modal-save").onclick = async () => {
        const btn = overlay.querySelector(".role-modal-save");
        btn.disabled = true; btn.textContent = "保存中…";
        const p = await this.updateRole(sel);
        close();
        if (p && onSaved) onSaved(p.role || "");
      };
      document.body.appendChild(overlay);
    },
  };

  window.Profile = Profile;
})();
