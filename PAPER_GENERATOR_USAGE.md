# 📚 论文生成系统使用指南

## 🚀 快速开始

### 1. 启动服务

```bash
cd agent
python -m backend.main
# 或
.\start.bat
```

服务默认运行在 `http://127.0.0.1:8000`

### 2. 访问论文生成页面

打开浏览器访问：

```
http://127.0.0.1:8000/paper
```

### 3. 使用流程

1. **选择题目** - 从下拉菜单选择一个建模问题
2. **点击生成** - 点击"开始生成论文"按钮
3. **等待完成** - 系统会自动完成以下步骤：
   - 搜索学术文献（arXiv + Semantic Scholar）
   - LLM 提取相关内容
   - 生成论文章节
   - 拼装完整论文
   - 导出 Word 文档
4. **下载结果** - 点击"下载 Word 文档"获取最终论文

---

## 📖 功能说明

### 内置题目

系统预设了 3 个示例题目：

1. **2023A: 应急救护车选址优化**
   - 搜索关键词: `emergency ambulance facility location optimization queueing theory`
   - 适合：运筹学、优化、排队论

2. **2022C: 社交网络影响力传播**
   - 搜索关键词: `social network influence propagation graph theory dynamics`
   - 适合：图论、网络科学、传播动力学

3. **2020A: 高中生暑期工作选择**
   - 搜索关键词: `multi-criteria decision making optimization preference aggregation`
   - 适合：多目标决策、偏好建模

### 生成过程

完整流程大约需要 **15-25 分钟**（取决于文献数量和章节复杂度）：

| 阶段 | 时间 | 说明 |
|------|------|------|
| 搜索文献 | ~10秒 | 搜索 arXiv 和 Semantic Scholar |
| 提取内容 | ~5-8分钟 | LLM 提取每篇论文的相关片段 |
| 生成章节 | ~10-15分钟 | 生成 6 个核心章节 |
| 拼装导出 | ~10秒 | 生成完整论文并导出 Word |

### 输出内容

生成的论文包含：

1. **Markdown 文件** - 纯文本格式，易于编辑
2. **Word 文档** - 可直接提交的 .docx 格式
3. **参考文献** - 自动生成的引用列表

---

## 🔧 技术细节

### API 端点

```
POST /api/paper/generate-full
```

**请求体**:
```json
{
  "problem_id": "2023A",
  "problem_title": "应急救护车选址优化",
  "problem_description": "问题描述...",
  "search_query": "emergency ambulance facility location"
}
```

**响应**: SSE 流式事件

- `progress` - 进度更新
- `section_done` - 章节完成
- `complete` - 全部完成
- `error` - 错误信息

### 数据存储

- **文献数据库**: `agent/data/paper_sessions.db` (SQLite)
- **论文数据库**: `agent/data/paper_sessions.db` (同一数据库)
- **Word 文件**: `agent/data/paper_{session_id}.docx`

---

## ⚠️ 注意事项

### 1. API 配置

确保 `.env` 文件中配置了正确的 API：

```env
LLM_API_KEY=sk-your-key-here
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-v4-pro
```

### 2. 网络连接

文献搜索需要访问：
- `arxiv.org` - arXiv API
- `api.semanticscholar.org` - Semantic Scholar API

如果网络受限，搜索可能失败（但论文仍可生成，只是没有文献支持）。

### 3. 生成时间

- 完整生成需要 15-25 分钟
- 不要关闭浏览器标签页
- 后台会持续更新进度

### 4. 存储空间

每篇论文大约占用：
- 数据库: ~500KB（文献片段）
- Word 文件: ~50-100KB

---

## 🐛 故障排除

### 问题 1: "请求失败"

**原因**: 后端服务未启动

**解决**: 
```bash
cd agent
python -m backend.main
```

### 问题 2: 搜索文献失败

**原因**: 网络连接问题或 API 限制

**解决**: 
- 检查网络连接
- 稍后重试（arXiv 和 S2 有速率限制）
- 论文仍会生成，只是没有文献引用

### 问题 3: LLM 调用失败

**原因**: API key 无效或余额不足

**解决**: 
- 检查 `.env` 中的 `LLM_API_KEY`
- 确认 API 账户有足够余额

### 问题 4: 生成卡住不动

**原因**: LLM 响应超时

**解决**: 
- 刷新页面重试
- 检查 `agent/data/app.log` 日志

---

## 📊 示例输出

生成的论文包含以下章节：

1. **Problem Restatement** - 问题重述
2. **Assumptions** - 模型假设
3. **Notation** - 符号说明
4. **Model Formulation** - 模型构建
5. **Model Solution** - 模型求解
6. **Results and Analysis** - 结果分析

每个章节都会：
- 引用相关学术文献
- 使用 LaTeX 数学公式
- 符合 HiMCM 论文格式

---

## 🔗 相关文档

- [完整技术文档](PAPER_GENERATION_WITH_LITERATURE.md)
- [项目结构说明](agent/STRUCTURE.md)
- [API 文档](agent/backend/main.py)

---

**最后更新**: 2026-08-19
