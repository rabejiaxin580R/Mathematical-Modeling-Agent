"""
建模方法库
为BUILD阶段提供方法选择引导
"""

# 方法库：按问题类型分类
METHOD_LIBRARY = {
    "optimization": {
        "category": "优化类问题",
        "keywords": ["最优", "最小", "最大", "分配", "选择", "规划", "调度"],
        "methods": [
            {
                "id": "opt.lp",
                "name": "线性规划 (Linear Programming)",
                "适用场景": "资源分配、成本最小化、产量最大化，目标函数和约束均为线性",
                "数学形式": "min c^T x, s.t. Ax ≤ b, x ≥ 0",
                "工具实现": [
                    "MATLAB: linprog(f, A, b, Aeq, beq, lb, ub)",
                    "Python: pulp.LpProblem() 或 scipy.optimize.linprog()",
                    "LINGO: 直接建模语言"
                ],
                "示例": "工厂生产计划、运输问题、投资组合优化"
            },
            {
                "id": "opt.integer",
                "name": "整数规划 (Integer Programming)",
                "适用场景": "0-1决策、设施选址、项目选择，决策变量必须为整数",
                "数学形式": "min c^T x, s.t. Ax ≤ b, x ∈ Z^n",
                "工具实现": [
                    "LINGO: 使用@GIN()声明整数变量",
                    "Python: pulp.LpInteger 或 mip.Model()",
                    "MATLAB: intlinprog(f, intcon, A, b)"
                ],
                "示例": "设施选址、任务分配、背包问题"
            },
            {
                "id": "opt.nonlinear",
                "name": "非线性规划 (Nonlinear Programming)",
                "适用场景": "目标函数或约束为非线性，如二次规划、几何规划",
                "数学形式": "min f(x), s.t. g(x) ≤ 0, h(x) = 0",
                "工具实现": [
                    "MATLAB: fmincon(fun, x0, A, b, Aeq, beq, lb, ub, nonlcon)",
                    "Python: scipy.optimize.minimize(method='SLSQP')",
                ],
                "示例": "最优控制、参数估计、工程设计优化"
            },
            {
                "id": "opt.dp",
                "name": "动态规划 (Dynamic Programming)",
                "适用场景": "多阶段决策、具有最优子结构和无后效性",
                "数学形式": "V(t) = max{r(t) + βV(t+1)}",
                "工具实现": [
                    "递归或迭代实现（Python/MATLAB）",
                    "Bellman方程求解"
                ],
                "示例": "资源分配、库存控制、路径规划"
            },
        ]
    },

    "evaluation": {
        "category": "评价/决策类问题",
        "keywords": ["评价", "决策", "选择", "排序", "权重", "指标"],
        "methods": [
            {
                "id": "eval.ahp",
                "name": "层次分析法 (AHP)",
                "适用场景": "多目标决策、各指标相互独立、需要专家打分",
                "数学形式": "构造判断矩阵A，计算特征向量w（权重），一致性检验CR<0.1",
                "工具实现": [
                    "手工计算：判断矩阵→特征向量→一致性检验",
                    "Python: ahpy库",
                    "MATLAB: 自编函数"
                ],
                "示例": "方案评选、供应商选择、投资决策"
            },
            {
                "id": "eval.anp",
                "name": "网络分析法 (ANP)",
                "适用场景": "指标之间相互依赖、网络结构决策",
                "数学形式": "超矩阵W，加权超矩阵W̄，极限超矩阵W∞",
                "工具实现": [
                    "Super Decisions软件",
                    "手工计算（较复杂）"
                ],
                "示例": "复杂系统评价、战略决策"
            },
            {
                "id": "eval.fuzzy",
                "name": "模糊综合评价",
                "适用场景": "定性与定量指标结合、模糊信息处理",
                "数学形式": "隶属度函数μ(x)，模糊矩阵R，综合评价B=W∘R",
                "工具实现": [
                    "MATLAB Fuzzy Logic Toolbox",
                    "Python: scikit-fuzzy"
                ],
                "示例": "教学质量评价、风险评估"
            },
            {
                "id": "eval.topsis",
                "name": "TOPSIS法",
                "适用场景": "多属性决策、指标量纲不同",
                "数学形式": "计算正理想解z+和负理想解z-，综合评价指数Ci",
                "工具实现": [
                    "Python/MATLAB: 矩阵运算实现",
                    "归一化→加权→距离计算→贴近度"
                ],
                "示例": "产品选择、绩效评估"
            },
            {
                "id": "eval.weightsum",
                "name": "加权求和评价",
                "适用场景": "简单多属性决策、线性加权",
                "数学形式": "S = Σ w_i * x_i, Σ w_i = 1",
                "工具实现": [
                    "直接计算（Excel/Python/MATLAB）"
                ],
                "示例": "学生成绩评定、简单排序"
            },
        ]
    },

    "prediction": {
        "category": "预测类问题",
        "keywords": ["预测", "趋势", "未来", "增长", "时间序列"],
        "methods": [
            {
                "id": "pred.timeseries",
                "name": "时间序列分析 (ARIMA)",
                "适用场景": "历史数据充足、有明显趋势或周期性",
                "数学形式": "ARIMA(p,d,q): (1-φL)^p (1-L)^d X_t = (1+θL)^q ε_t",
                "工具实现": [
                    "MATLAB: arima模型",
                    "Python: statsmodels.tsa.arima.ARIMA",
                    "R: forecast包"
                ],
                "示例": "销售预测、股价预测、负荷预测"
            },
            {
                "id": "pred.grey",
                "name": "灰色预测 GM(1,1)",
                "适用场景": "小样本预测、数据缺失或不完整",
                "数学形式": "累加生成序列X^(1)，建立微分方程dx/dt + ax = b",
                "工具实现": [
                    "手工计算（步骤较简单）",
                    "MATLAB/Python自编函数"
                ],
                "示例": "人口预测、能源消耗预测"
            },
            {
                "id": "pred.linreg",
                "name": "线性回归",
                "适用场景": "因变量与自变量线性关系、因果分析",
                "数学形式": "y = β0 + β1*x1 + ... + βn*xn + ε",
                "工具实现": [
                    "MATLAB: fitlm() 或 regress()",
                    "Python: sklearn.linear_model.LinearRegression",
                    "SPSS: 回归分析模块"
                ],
                "示例": "需求预测、成本估算、相关性分析"
            },
            {
                "id": "pred.nonlinear",
                "name": "非线性拟合",
                "适用场景": "非线性趋势、指数增长/衰减、逻辑曲线",
                "数学形式": "y = f(x, β)，如指数y=ae^(bx)、对数y=a+b*ln(x)",
                "工具实现": [
                    "MATLAB: fit() 或 nlinfit()",
                    "Python: scipy.optimize.curve_fit()"
                ],
                "示例": "人口增长（Logistic）、学习曲线、疫情传播"
            },
        ]
    },

    "classification": {
        "category": "分类/识别类问题",
        "keywords": ["分类", "识别", "判别", "聚类", "模式"],
        "methods": [
            {
                "id": "cls.logistic",
                "name": "逻辑回归",
                "适用场景": "二分类问题、概率预测",
                "数学形式": "P(y=1|x) = 1/(1+e^(-β^T x))",
                "工具实现": [
                    "Python: sklearn.linear_model.LogisticRegression",
                    "MATLAB: fitglm() with binomial distribution",
                    "SPSS: 二元Logistic回归"
                ],
                "示例": "疾病诊断、信用评分、广告点击预测"
            },
            {
                "id": "cls.svm",
                "name": "支持向量机 (SVM)",
                "适用场景": "分类问题、非线性可分、高维数据",
                "数学形式": "max-margin分类器，核技巧K(x,x')",
                "工具实现": [
                    "Python: sklearn.svm.SVC",
                    "MATLAB: fitcsvm()"
                ],
                "示例": "图像分类、文本分类、异常检测"
            },
            {
                "id": "cls.tree",
                "name": "决策树",
                "适用场景": "分类或回归、规则可解释",
                "数学形式": "信息增益、基尼系数分裂",
                "工具实现": [
                    "Python: sklearn.tree.DecisionTreeClassifier",
                    "MATLAB: fitctree()"
                ],
                "示例": "医疗诊断、信贷审批、推荐系统"
            },
        ]
    },

    "simulation": {
        "category": "仿真/模拟类问题",
        "keywords": ["仿真", "模拟", "蒙特卡洛", "排队", "系统"],
        "methods": [
            {
                "id": "sim.montecarlo",
                "name": "蒙特卡洛模拟",
                "适用场景": "不确定性分析、风险评估、复杂系统",
                "数学形式": "E[f(X)] ≈ (1/N)Σf(X_i), X_i~p(x)",
                "工具实现": [
                    "MATLAB/Python: 随机数生成+循环模拟",
                    "大规模并行加速"
                ],
                "示例": "投资风险、项目工期、系统可靠性"
            },
            {
                "id": "sim.queue",
                "name": "排队论模型",
                "适用场景": "服务系统、等待时间分析",
                "数学形式": "M/M/c模型: λ到达率, μ服务率, ρ=λ/(cμ)利用率",
                "工具实现": [
                    "解析公式计算（MATLAB/Python）",
                    "离散事件仿真（SimPy）"
                ],
                "示例": "呼叫中心、医院急诊、交通流量"
            },
        ]
    },
}


def classify_problem_type(problem_text: str) -> list:
    """
    根据问题描述，识别问题类型
    返回: 可能的类型列表，按相关性排序
    """
    problem_lower = problem_text.lower()

    scores = []
    for ptype, pdata in METHOD_LIBRARY.items():
        keywords = pdata["keywords"]
        score = sum(1 for kw in keywords if kw in problem_lower)
        if score > 0:
            scores.append((ptype, score, pdata["category"]))

    # 按得分排序
    scores.sort(key=lambda x: -x[1])

    return [(t, cat) for t, s, cat in scores]


def get_recommended_methods(problem_text: str, top_n: int = 3) -> list:
    """
    根据问题类型，推荐建模方法

    Args:
        problem_text: 问题描述
        top_n: 返回前N个推荐方法

    Returns:
        推荐方法列表，每个方法包含完整信息
    """
    problem_types = classify_problem_type(problem_text)

    if not problem_types:
        # 如果无法识别，返回通用方法
        return [
            METHOD_LIBRARY["optimization"]["methods"][0],  # 线性规划
            METHOD_LIBRARY["evaluation"]["methods"][4],    # 加权求和
            METHOD_LIBRARY["prediction"]["methods"][2],    # 线性回归
        ][:top_n]

    # 从最相关的类型中选择方法
    recommended = []
    for ptype, category in problem_types:
        methods = METHOD_LIBRARY[ptype]["methods"]
        recommended.extend(methods)

        if len(recommended) >= top_n:
            break

    return recommended[:top_n]


def format_methods_for_prompt(methods: list) -> str:
    """
    将推荐方法格式化为Prompt文本
    """
    lines = []

    for i, method in enumerate(methods, 1):
        lines.append(f"\n### 方法{i}: {method['name']}")
        lines.append(f"- **适用场景**: {method['适用场景']}")
        lines.append(f"- **数学形式**: {method['数学形式']}")
        lines.append(f"- **工具实现**:")
        for tool in method['工具实现']:
            lines.append(f"  - {tool}")
        lines.append(f"- **示例**: {method['示例']}")

    return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    problem = "高中生需要从多个暑期工作中选择最优方案，考虑财务收益、生活质量、个人兼容性等因素"

    print("问题类型识别:")
    types = classify_problem_type(problem)
    for t, cat in types:
        print(f"  - {cat} ({t})")

    print("\n推荐方法:")
    methods = get_recommended_methods(problem, top_n=3)
    print(format_methods_for_prompt(methods))
