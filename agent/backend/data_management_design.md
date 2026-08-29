# 通用数据管理架构设计

## 核心问题

当前问题：
1. 用户上传数据表（或系统生成测试数据）时，完整数据塞进prompt导致输入爆炸
2. 只针对特定题目（2020 Problem A）设计了"10个JSON学生"的hack方案
3. 没有通用的数据存储、采样、摘要机制

## 设计原则

1. **结构化存储**：所有数据（用户上传/系统生成）存入数据库表，不塞进Markdown
2. **分层访问**：
   - Schema层：表结构、列名、数据类型
   - 统计层：行数、数值范围、分布特征
   - 采样层：代表性案例（5-10行）
3. **上下文节约**：prompt里只传摘要+采样，完整数据在数据库里
4. **通用性**：支持任意题目、任意数据源、任意规模

## 表结构设计

### 主表：session_datasets

```sql
CREATE TABLE IF NOT EXISTS session_datasets (
    session_id    TEXT NOT NULL REFERENCES paper_sessions(session_id) ON DELETE CASCADE,
    dataset_id    TEXT NOT NULL CHECK(dataset_id GLOB 'DS[0-9]*' AND length(dataset_id) >= 3),
    dataset_name  TEXT NOT NULL,                    -- 用户可读名称，如 "Summer Job Test Cases"
    source_type   TEXT NOT NULL CHECK(source_type IN ('user_upload', 'generated', 'scraped')),
    total_rows    INTEGER NOT NULL CHECK(total_rows > 0),
    created_at    TEXT NOT NULL CHECK(created_at GLOB '????-??-??T??:??:??*'),
    schema_json   TEXT NOT NULL,                    -- JSON字符串：列定义 [{"name":"age","type":"int","unit":"years"},...]
    stats_json    TEXT,                             -- JSON字符串：统计信息 {"age":{"min":18,"max":65,"mean":42.3},...}
    sample_rows   TEXT,                             -- JSON字符串：采样的5-10行数据
    section_key   TEXT,                             -- 关联章节（通常是analyze）
    PRIMARY KEY (session_id, dataset_id)
);
```

**字段说明**：

- `dataset_id`: DS1, DS2, DS10... 格式
- `source_type`: 
  - `user_upload`: 用户上传的Excel/CSV
  - `generated`: 系统生成的测试数据
  - `scraped`: 从网页抓取的数据
- `schema_json`: 存储列结构
  ```json
  [
    {"name": "student_name", "type": "string"},
    {"name": "age", "type": "int", "unit": "years"},
    {"name": "income", "type": "float", "unit": "USD"}
  ]
  ```
- `stats_json`: 数值列的统计摘要
  ```json
  {
    "age": {"min": 18, "max": 65, "mean": 42.3, "std": 12.1},
    "income": {"min": 15000, "max": 120000, "mean": 45000}
  }
  ```
- `sample_rows`: 采样的代表性案例（JSON数组）
  ```json
  [
    {"student_name": "Alice", "age": 22, "income": 35000},
    {"student_name": "Bob", "age": 45, "income": 78000},
    ...
  ]
  ```

### 辅助表：dataset_rows（可选，用于大数据集）

如果数据量超过1000行，完整数据存入独立表：

```sql
CREATE TABLE IF NOT EXISTS dataset_rows (
    session_id    TEXT NOT NULL,
    dataset_id    TEXT NOT NULL,
    row_id        INTEGER NOT NULL CHECK(row_id >= 1),
    row_data      TEXT NOT NULL,                    -- JSON字符串：一行数据
    PRIMARY KEY (session_id, dataset_id, row_id),
    FOREIGN KEY (session_id, dataset_id) REFERENCES session_datasets(session_id, dataset_id) ON DELETE CASCADE
);
```

**使用场景**：
- 小数据集（≤100行）：直接存在session_datasets.sample_rows
- 中数据集（100-1000行）：完整数据存dataset_rows，采样10行存session_datasets
- 大数据集（>1000行）：完整数据存dataset_rows，采样20行+统计摘要

## 数据流程

### 场景1：系统生成测试数据（如2020 Problem A）

```python
# Stage 1: 生成虚拟数据
students_json = generate_fictional_test_data(problem_md, model_context, num=10)

# Stage 2: 存入数据库
dataset_id = "DS1"
schema = extract_schema(students_json)  # 自动推断列结构
stats = compute_stats(students_json)    # 计算统计摘要
sample = sample_rows(students_json, n=10)  # 采样（这里全部都是采样）

conn.execute("""
    INSERT INTO session_datasets 
    (session_id, dataset_id, dataset_name, source_type, total_rows, 
     schema_json, stats_json, sample_rows, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (session_id, dataset_id, "Summer Job Test Cases", "generated", 10,
      json.dumps(schema), json.dumps(stats), json.dumps(sample), now()))

# Stage 3: 生成Results章节
# prompt里只传：
#   - "You have DS1: Summer Job Test Cases with 10 students"
#   - schema: ["name", "preferences", "constraints", "options"]
#   - stats: "preferences.earnings_weight: mean=0.5, range=[0.1, 0.9]"
#   - sample: 前3个学生的完整数据
# 而不是把10个学生的完整JSON全塞进去
```

### 场景2：用户上传真实数据

```python
# 用户上传 population_data.csv（500行）
df = pd.read_csv(uploaded_file)

# 存入数据库
dataset_id = "DS1"
schema = infer_schema_from_df(df)
stats = compute_stats_from_df(df)
sample = df.sample(n=min(20, len(df))).to_dict('records')

# 如果数据量大，完整数据存dataset_rows
if len(df) > 100:
    for idx, row in df.iterrows():
        conn.execute("""
            INSERT INTO dataset_rows (session_id, dataset_id, row_id, row_data)
            VALUES (?, ?, ?, ?)
        """, (session_id, dataset_id, idx+1, json.dumps(row.to_dict())))

# session_datasets存摘要
conn.execute("""
    INSERT INTO session_datasets ...
    VALUES (?, ?, "Population Data", "user_upload", 500, ...)
""")

# Results生成时：
# prompt: "User uploaded DS1 with 500 records. Schema: [age, income, education]. 
#          Age range: 18-65 (mean 42.3). See sample cases below..."
```

## Prompt构建逻辑

### 修改_generate_results_with_data()

```python
def _build_dataset_context(conn, session_id: str) -> str:
    """从session_datasets读取数据摘要，构建简洁的上下文"""
    
    datasets = conn.execute("""
        SELECT dataset_id, dataset_name, source_type, total_rows,
               schema_json, stats_json, sample_rows
        FROM session_datasets
        WHERE session_id = ?
    """, (session_id,)).fetchall()
    
    if not datasets:
        return ""
    
    context_parts = []
    for ds in datasets:
        schema = json.loads(ds['schema_json'])
        stats = json.loads(ds['stats_json']) if ds['stats_json'] else {}
        sample = json.loads(ds['sample_rows'])
        
        # 简洁描述
        desc = f"**{ds['dataset_id']}** ({ds['dataset_name']}): {ds['total_rows']} records"
        
        # Schema
        cols = ", ".join([col['name'] for col in schema])
        desc += f"\n  Columns: {cols}"
        
        # 关键统计
        if stats:
            stats_summary = []
            for col, stat in list(stats.items())[:3]:  # 最多3个列
                if 'mean' in stat:
                    stats_summary.append(f"{col}: {stat['min']}-{stat['max']} (mean {stat['mean']:.1f})")
            if stats_summary:
                desc += f"\n  Key stats: " + "; ".join(stats_summary)
        
        # 采样（只显示前3行的关键字段）
        if sample:
            desc += f"\n  Sample (first 3 of {len(sample)}):"
            for i, row in enumerate(sample[:3]):
                # 只显示前4个字段
                fields = list(row.items())[:4]
                row_str = ", ".join([f"{k}={v}" for k, v in fields])
                desc += f"\n    {i+1}. {row_str}"
        
        context_parts.append(desc)
    
    return "\n\n".join(context_parts)
```

### 新的Stage 2 prompt

```python
def _generate_results_with_data(
    conn,
    session_id: str,
    problem_md: str,
    model_context: str,
    conversation_text: str,
    kb_context: str
) -> str | None:
    """Stage 2：从数据库读取数据摘要，而不是完整数据"""
    
    # 构建数据上下文（精简版）
    dataset_context = _build_dataset_context(conn, session_id)
    
    prompt = f"""# Your task: Write the **Results and Analysis** section

## Problem
{problem_md[:2000]}

## Model (from previous sections)
{model_context[:1000]}

## Available Test Data

{dataset_context}

**Note**: Full data is stored in the database. Use the schema, statistics, and sample cases above to perform your analysis.

## Requirements

1. **Apply model to ALL test cases** mentioned in the dataset descriptions above
2. **Create summary tables**: 
   - Table 1: Test case characteristics
   - Table 2: Model outputs and recommendations
3. **Show detailed calculations** for 2-3 representative cases
4. **Analysis**: How do different inputs lead to different outputs?

Write ONLY the section content. Start with the section heading.
"""
    
    return _call_writer(_WRITER_PERSONA, prompt)
```

## 优势

1. **上下文节约**：
   - 之前：10个学生JSON = 5000字符
   - 现在：数据摘要 = 500字符
   - 节约90%的输入token

2. **通用性**：
   - 支持任意数据源（用户上传/系统生成）
   - 支持任意规模（10行到10000行）
   - 支持任意题目类型

3. **可扩展性**：
   - 未来可以加入数据可视化（从dataset_rows生成图表）
   - 可以支持多数据集联合分析
   - 可以实现数据版本控制

4. **符合你的设计哲学**：
   - 结构化存储（不是Markdown里塞数据）
   - 分阶段提取（symbols/model_cards/datasets都用同样模式）
   - 上下文管理（防止输入爆炸）

## 实施步骤

1. 在paper_db.py添加session_datasets和dataset_rows表定义
2. 实现数据存储函数：store_dataset()
3. 实现摘要构建函数：_build_dataset_context()
4. 修改_generate_fictional_test_data()：生成后立即存库
5. 修改_generate_results_with_data()：从库读摘要而不是用完整JSON
6. 测试：生成一篇论文，验证Results章节完整性
