<h1 align="center">📐 数学建模助教 <sup>v2.3.0</sup></h1>

<p align="center"><i>把想法变成模型，把模型写成论文。</i></p>

<p align="center">
基于 40 小时数学建模课程知识库的 AI 智能助教<br>
自动拆解问题 · 有出处地答疑 · 写代码跑代码 · 手把手教零基础
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-v2.3.0-8b5cf6?style=for-the-badge" alt="Version">
  <img src="https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-2ea44f?style=for-the-badge" alt="Platform">
  <img src="https://img.shields.io/badge/tests-232%20passed-3fb950?style=for-the-badge" alt="Tests">
  <img src="https://img.shields.io/badge/license-MIT-yellow?style=for-the-badge" alt="License">
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/rabejiaxin580R/Mathematical-Modeling-Agent?style=social" alt="Stars">
  <img src="https://img.shields.io/github/forks/rabejiaxin580R/Mathematical-Modeling-Agent?style=social" alt="Forks">
  <img src="https://img.shields.io/github/watchers/rabejiaxin580R/Mathematical-Modeling-Agent?style=social" alt="Watchers">
</p>

<table align="center">
<tr><td>
<pre>
        /\_/\          ∑  ∫  ∂
       ( o.o )   ──▶  ┌─────────────┐
        > ^ <         │  建模助教   │
       /     \        └──────┬──────┘
          │                  │
          └─────────┬────────┘
                    ▼
     题目  →  讨论  →  验证  →  论文
</pre>
</td></tr>
</table>

<p align="center"><sub>今日份的建模小猫：陪你从第一行假设，走到最后一页论文。</sub></p>

<p align="center">
  🎁 <b>新用户注册即送 150 万 Token 免费额度，零成本上手！</b> 🎁
</p>

---

## 🆕 v2.3.0 更新亮点

| 模块 | 这次做了什么 |
|:---|:---|
| 🧭 **持久化工作流** | 代码验证与对话在后台继续，切换页面或刷新后可以恢复 |
| ✅ **步骤成果** | 每一步都有目标与验收清单，成果由你确认后才进入论文材料 |
| 🗂 **研究材料** | 讨论摘录、验证记录、论文草稿保留来源和版本，随时回看依据 |
| 🎨 **工作台重设计** | 讨论、代码、结果、论文材料集中切换，长对话也能稳稳接住 |
| 🐞 **构建修复** | 修正 `mathml2omml` 依赖版本，Windows / macOS / Linux 三平台恢复构建 |

### 从「聊着聊着就散了」到「一路都有记录」

| 以前 | 现在 |
|:---|:---|
| 切页或刷新后，任务状态可能丢失 | 后台继续运行，回来接着看 |
| 讨论、代码、结果、论文分散在不同地方 | 同一个工作台里切换 |
| 运行成功容易被误当成结论成立 | 运行记录与验收清单分开，结论由你确认 |
| 长对话容易挤掉关键上下文 | 有出处的上下文预算，保留可追溯来源 |


> 📋 [完整更新日志](CHANGELOG.md)

<details>
<summary>📦 v2.2 更新亮点</summary>

| 🎯 | 内容 |
|---|------|
| 📝 | **一键生成建模论文**：大纲→全文、自动配图、文献溯源，生成结果一键导出 Word |
| 🔍 | **在线文献检索**：检索服务迁到网关（鉴权不计费、结果缓存 7 天），agent 端网关优先、直连兜底 |
| 🎭 | **双风格等待界面**：剧情 / 终端两种日志可切换，章节进度条 + 心跳 + 卡住提示 |
| 📊 | **自动配图**：从数据表格自动选型成图（柱状 / 折线），base64 注入正文 |

</details>

<details>
<summary>📦 v2.1 更新亮点</summary>

| 🎯 | 内容 |
|---|------|
| 🐛 | **修复 AI 回答被"吞掉"的严重 Bug**：修复流式回答有时在眼前消失，回答稳稳到底 |
| ✨ | **知识库按评级分层**：检索结果随你的 L1~L5 水平自动调整——萌新多给「一句话总结 + 举例」，高手多给「公式 + 推导 + 代码」 |
| 🔧 | **角色 × 评级独立**：「内容侧重」由分工决定、「难度深度」由评级决定，两个维度互不干扰 |

</details>

<details>
<summary>📦 v2.0 更新亮点</summary>

| 🎯 | 内容 |
|---|------|
| 📝 | **入门测评系统**：55 道题 × 10 大模块 × 3 个难度，自动定级 L1~L5，告诉你弱项在哪、该从何学起 |
| 🎁 | **免费 Token 网关**：我们搭了独立站点 `math-modeling.top`，注册即送 **150 万 Token**，无需绑卡付费 |
| 📧 | **一键注册领额度**：邮箱 + 验证码 30 秒搞定，控制台生成 API Key 粘贴即用 |
| 🍎 | **macOS 重要提醒**：千万不要把应用放在 iCloud 云盘里！ |

</details>

---

## ✨ 功能一览

| 功能 | 说明 |
|------|------|
| 🧭 **做题工作流** 🆕 | 题目拆解 → 逐步讨论 → 代码验证 → 成果确认 → 论文材料，全程可恢复 |
| 💬 **非阻塞对话** | 代码和回答在后台继续，切页、刷新不打断；回来接着看 |
| 🗂 **研究手记** 🆕 | 讨论摘录、验证记录、材料版本和来源集中管理 |
| 📝 **论文生成** | 大纲 → 全文，自动配图、文献溯源、导出 Word |
| 📝 **入门测评** | 55 道精选题，自动定级 + 弱项分析 + 推荐学习路线 |
| 📚 **学习模式** | 顺着知识图谱，从基础概念一步步点亮建模技能树 |
| 🔧 **做建模** | 自由工作台（编辑器 + 终端 + AI），AI 陪你一步步解题 |
| 📝 **真题练习** | 历年 HiMCM 真题分步练，AI 按评分要点打分点评 |

---

## 🧱 一条完整的建模路径

```text
题目 / 数据 / 初步想法
          │
          ▼
   讨论与知识检索
          │
          ▼
   代码验证（后台运行）
          │
          ▼
   学生确认本步成果
          │
          ▼
论文材料 → 大纲 → 正文 → Word
```

---

## 📈 关注曲线
<summary>📌 展开实时 Star 曲线（Star History）</summary>

<p align="center">
  <a href="https://star-history.com/#rabejiaxin580R/Mathematical-Modeling-Agent&Date">
    <img src="https://api.star-history.com/svg?repos=rabejiaxin580R/Mathematical-Modeling-Agent&type=Date" alt="Star History Chart">
  </a>
</p>


---

## 📦 下载

开箱即用，**自带完整 Python 运行环境，无需安装任何东西**。

| 系统 | 下载 | 说明 |
|------|------|------|
| 🪟 **Windows 安装版** | [MathModelingAgent-Setup-v2.3.0.exe](https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/releases/download/v2.3.0/MathModelingAgent-Setup-v2.3.0.exe) | 图形安装，约 207 MB |
| 🍎 **macOS ARM64** | [MathModelingAgent-macos-arm64.tar.gz](https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/releases/download/v2.3.0/MathModelingAgent-macos-arm64.tar.gz) | Apple Silicon，约 271 MB |
| 🐧 **Linux x86_64** | [MathModelingAgent-linux-x86_64.tar.gz](https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/releases/download/v2.3.0/MathModelingAgent-linux-x86_64.tar.gz) | 解压后运行 `./启动助教`，约 464 MB |

> 🍎 macOS 当前提供 Apple Silicon（arm64）包；Intel Mac 暂未提供。
>
> 💡 所有包都自带 Python 运行环境，不需要另外安装 Python。

---

## 🚀 快速上手

1. **下载** → 安装 / 解压 → 双击启动
2. 浏览器自动打开 → 跟着向导连接 AI（免费额度 or 自己的 Key）
3. 先做**入门测评**看看自己什么水平 → 跟着推荐路线学
4. 有题目就直接**新建项目**，从问题分析开始一步步做
5. 每步成果确认后，整理成论文并导出 Word

> 📖 详细使用说明见 **[使用指南](使用指南.md)**

---

## 🎁 免费领 Token

我们部署了独立的 API 网关，**专门给数学建模助教用户提供免费额度**：

| 步骤 | 操作 |
|:--:|------|
| 1 | 启动助教 → 选「用我们提供的免费额度」→「打开控制台注册」 |
| 2 | 浏览器打开 `https://math-modeling.top` → 输入邮箱 → 收验证码 |
| 3 | 设置密码 → 注册成功 → **自动到账 150 万 Token** 💰 |
| 4 | 控制台点「生成 API Key」→ 复制 `sk-xxx` |
| 5 | 回到助教向导粘贴 → 保存 → 开用！ |

> 🔑 你的 Key 直连网关，上游密钥不暴露。额度用完可联系管理员免费续期。


## ⚠️ 安全提醒

- 模型生成的 Python 代码会在你本机以当前用户权限运行，前端会展示代码供审阅，请仅在信任环境中使用
- 🍎 **macOS 用户：不要将本应用放在 iCloud 云盘！会导致程序无法运行！**

---

<p align="center">
  <b>📐 用模型解释世界，用论文留下思考。</b><br><br>
  <a href="https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent">⭐ Star</a> ·
  <a href="https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/issues">🐛 Issue</a> ·
  <a href="https://github.com/rabejiaxin580R/Mathematical-Modeling-Agent/releases">📦 Releases</a>
</p>
