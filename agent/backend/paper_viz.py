"""论文可视化生成模块

自动为HiMCM论文生成图表：
1. Sensitivity Analysis的曲线图（utility vs parameter）
2. Results的对比柱状图（学生utility分布）
3. 其他常见可视化类型

生成base64编码的图片，直接嵌入Markdown
"""
import base64
import io
import re
from typing import Dict, List, Optional, Tuple
import matplotlib
matplotlib.use('Agg')  # 无GUI后端
import matplotlib.pyplot as plt
import numpy as np

# 设置中文字体（如果需要）
plt.rcParams['font.sans-serif'] = ['Arial']
plt.rcParams['axes.unicode_minus'] = False

# 设置默认样式
plt.style.use('seaborn-v0_8-darkgrid')


def _to_float(s):
    """把单元格字符串解析为 float；失败返回 None。

    只把「数字开头」的单元格当数值（如 "0.58"、"$5000"、"45 km/h"），
    避免把 "J7"、"Case 1"、"R3" 这类含数字的标签/编号误判成数值列。
    """
    if not isinstance(s, str):
        return None
    t = s.strip().replace(",", "").strip()
    if not t:
        return None
    t = t.replace("$", "").replace("%", "").replace("£", "").replace("¥", "").strip()
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        # 数字开头的单元格（如 "45 km/h" → 45），其余视为非数值
        m = re.match(r'-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?', t)
        return float(m.group(0)) if m else None


class PaperVisualizer:
    """论文可视化生成器"""

    def __init__(self, dpi: int = 150, figsize: Tuple[int, int] = (8, 5)):
        self.dpi = dpi
        self.figsize = figsize

    def _fig_to_base64(self, fig) -> str:
        """将matplotlib图表转为base64字符串"""
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=self.dpi, bbox_inches='tight')
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode('utf-8')
        plt.close(fig)
        return img_base64

    def _fig_to_markdown(self, fig, caption: str, label: str = "") -> str:
        """将图表转为Markdown格式（data URI）"""
        img_base64 = self._fig_to_base64(fig)
        data_uri = f"data:image/png;base64,{img_base64}"

        md = f"\n![{caption}]({data_uri})\n\n"
        if caption:
            md += f"**Figure**: {caption}\n\n"

        return md

    def chart_table(self, headers, rows, caption: str = "") -> str:
        """从 Markdown 表格数据生成通用图表（自动选横向柱状 / 分组柱状 / 折线）。

        headers: 表头字符串列表；rows: 数据行（每个 cell 为字符串）。
        数据不足或无法成图时返回 ""；成图返回 Markdown 图片字符串（alt 当图注，
        交给 export_docx 的 ![alt](data:...) 约定直接嵌入）。
        """
        if not headers or len(rows) < 2:
            return ""

        ncol = len(headers)
        # 逐列判定是否数值列（至少 2 个数值点，且覆盖一半以上行）
        numeric_cols = []
        for c in range(ncol):
            vals = [_to_float(r[c]) for r in rows if c < len(r)]
            vals = [v for v in vals if v is not None]
            if len(vals) >= 2 and len(vals) >= max(1, len(rows) * 0.5):
                numeric_cols.append(c)
        if not numeric_cols:
            return ""

        # 找一个非数值列当标签列（优先第一列）
        label_col = next((c for c in range(ncol) if c not in numeric_cols), None)
        labels = [
            (r[label_col] if (label_col is not None and label_col < len(r)) else f"#{i+1}")
            for i, r in enumerate(rows)
        ]

        if not caption:
            caption = self._auto_caption(headers, label_col, numeric_cols)

        fig, ax = plt.subplots(figsize=self.figsize)

        if label_col is not None and len(numeric_cols) == 1:
            c = numeric_cols[0]
            values = [_to_float(r[c]) or 0.0 for r in rows]
            ypos = list(range(len(labels)))[::-1]
            ax.barh(ypos, values, color="#4c78a8", edgecolor="black", linewidth=0.5)
            ax.set_yticks(ypos)
            ax.set_yticklabels(labels, fontsize=9)
            ax.set_xlabel(headers[c], fontsize=10)
            for yi, v in zip(ypos, values):
                ax.text(v, yi, f"{v:g}", va="center", ha="left", fontsize=8)
        elif label_col is not None and len(numeric_cols) >= 2:
            series = [[_to_float(r[c]) or 0.0 for r in rows] for c in numeric_cols]
            x = np.arange(len(labels))
            width = 0.8 / len(series)
            for si, s in enumerate(series):
                off = (si - (len(series) - 1) / 2) * width
                ax.bar(x + off, s, width, label=headers[numeric_cols[si]],
                       edgecolor="black", linewidth=0.5)
            ax.set_xticks(x)
            ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
            ax.legend(fontsize=8)
        elif len(numeric_cols) >= 2:
            xs = [_to_float(r[numeric_cols[0]]) or 0.0 for r in rows]
            for c in numeric_cols[1:]:
                ys = [_to_float(r[c]) or 0.0 for r in rows]
                ax.plot(xs, ys, marker="o", label=headers[c], linewidth=1.5)
            ax.set_xlabel(headers[numeric_cols[0]], fontsize=10)
            ax.legend(fontsize=8)
        else:
            plt.close(fig)
            return ""

        ax.grid(True, alpha=0.3)
        ax.set_title(caption, fontsize=12, fontweight="bold")
        plt.tight_layout()
        img_base64 = self._fig_to_base64(fig)
        return f"\n![{caption}](data:image/png;base64,{img_base64})\n"

    @staticmethod
    def _auto_caption(headers, label_col, numeric_cols) -> str:
        label = headers[label_col] if label_col is not None else "case"
        vals = ", ".join(headers[c] for c in numeric_cols[:2])
        return f"{vals} by {label}"

    # ========== Sensitivity Analysis 可视化 ==========

    def plot_utility_vs_weight(
        self,
        weight_name: str,
        weight_values: np.ndarray,
        utilities: Dict[str, np.ndarray],
        current_weight: float,
        recommended_job: str,
        caption: str = ""
    ) -> str:
        """
        绘制 utility vs preference weight 曲线

        Args:
            weight_name: 权重名称（如 "λ_E"）
            weight_values: 权重值数组（如0到1）
            utilities: {job_name: utility_array} 字典
            current_weight: 当前基线权重值
            recommended_job: 推荐的工作
            caption: 图表标题

        Returns:
            Markdown格式的图表
        """
        fig, ax = plt.subplots(figsize=self.figsize)

        # 绘制每个工作的utility曲线
        for job_name, utility_array in utilities.items():
            linestyle = '-' if job_name == recommended_job else '--'
            linewidth = 2.5 if job_name == recommended_job else 1.5
            ax.plot(weight_values, utility_array,
                   label=job_name, linestyle=linestyle, linewidth=linewidth)

        # 标记当前权重值
        ax.axvline(current_weight, color='red', linestyle=':',
                  linewidth=1.5, alpha=0.7, label=f'Current {weight_name} = {current_weight:.2f}')

        ax.set_xlabel(f'Preference weight {weight_name}', fontsize=11)
        ax.set_ylabel('Total utility score', fontsize=11)
        ax.set_title(caption or f'Utility sensitivity to {weight_name}', fontsize=12, fontweight='bold')
        ax.legend(loc='best', fontsize=9)
        ax.grid(True, alpha=0.3)
        ax.set_xlim(weight_values[0], weight_values[-1])
        ax.set_ylim(0, 1)

        return self._fig_to_markdown(fig, caption)

    def plot_sensitivity_heatmap(
        self,
        param1_name: str,
        param1_values: np.ndarray,
        param2_name: str,
        param2_values: np.ndarray,
        recommendation_grid: np.ndarray,
        job_labels: List[str],
        caption: str = ""
    ) -> str:
        """
        绘制二维参数空间的推荐热力图

        Args:
            param1_name: 参数1名称
            param1_values: 参数1的值
            param2_name: 参数2名称
            param2_values: 参数2的值
            recommendation_grid: 推荐结果矩阵（job index）
            job_labels: 工作名称列表
            caption: 图表标题
        """
        fig, ax = plt.subplots(figsize=(self.figsize[0], self.figsize[0]*0.8))

        im = ax.imshow(recommendation_grid, aspect='auto', cmap='tab10',
                      extent=[param1_values[0], param1_values[-1],
                             param2_values[0], param2_values[-1]],
                      origin='lower')

        ax.set_xlabel(param1_name, fontsize=11)
        ax.set_ylabel(param2_name, fontsize=11)
        ax.set_title(caption or 'Recommendation regions', fontsize=12, fontweight='bold')

        # 添加颜色条
        cbar = plt.colorbar(im, ax=ax, ticks=range(len(job_labels)))
        cbar.set_label('Recommended job', fontsize=10)
        cbar.ax.set_yticklabels(job_labels, fontsize=9)

        return self._fig_to_markdown(fig, caption)

    # ========== Results 可视化 ==========

    def plot_utility_comparison(
        self,
        student_names: List[str],
        utilities: List[float],
        jobs: List[str],
        highlight_threshold: float = 0.7,
        caption: str = ""
    ) -> str:
        """
        绘制学生utility对比柱状图

        Args:
            student_names: 学生名称列表
            utilities: utility值列表
            jobs: 推荐的工作列表
            highlight_threshold: 高亮阈值（utility > threshold）
            caption: 图表标题
        """
        fig, ax = plt.subplots(figsize=(self.figsize[0], self.figsize[1]))

        # 按utility排序
        sorted_indices = np.argsort(utilities)[::-1]
        sorted_students = [student_names[i] for i in sorted_indices]
        sorted_utilities = [utilities[i] for i in sorted_indices]
        sorted_jobs = [jobs[i] for i in sorted_indices]

        # 颜色编码：不同工作不同颜色
        unique_jobs = list(set(jobs))
        colors = plt.cm.tab10(np.linspace(0, 1, len(unique_jobs)))
        job_color_map = {job: colors[i] for i, job in enumerate(unique_jobs)}
        bar_colors = [job_color_map[job] for job in sorted_jobs]

        bars = ax.barh(range(len(sorted_students)), sorted_utilities,
                      color=bar_colors, edgecolor='black', linewidth=0.5)

        ax.set_yticks(range(len(sorted_students)))
        ax.set_yticklabels(sorted_students, fontsize=10)
        ax.set_xlabel('Total utility score', fontsize=11)
        ax.set_title(caption or 'Student utility comparison', fontsize=12, fontweight='bold')
        ax.set_xlim(0, 1)
        ax.grid(axis='x', alpha=0.3)

        # 添加图例
        legend_elements = [plt.Rectangle((0,0),1,1, fc=job_color_map[job],
                                        edgecolor='black', linewidth=0.5, label=job)
                          for job in unique_jobs]
        ax.legend(handles=legend_elements, loc='lower right', fontsize=9,
                 title='Recommended job')

        # 标注utility值
        for i, (bar, utility) in enumerate(zip(bars, sorted_utilities)):
            ax.text(utility + 0.02, i, f'{utility:.3f}',
                   va='center', fontsize=8)

        return self._fig_to_markdown(fig, caption)

    def plot_factor_breakdown(
        self,
        student_name: str,
        factor_names: List[str],
        factor_scores: List[float],
        weights: List[float],
        caption: str = ""
    ) -> str:
        """
        绘制单个学生的utility因子分解图

        Args:
            student_name: 学生名称
            factor_names: 因子名称（如 ["Earnings", "Recreation", "Compatibility"]）
            factor_scores: 归一化得分
            weights: 权重
            caption: 图表标题
        """
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(self.figsize[0]*1.2, self.figsize[1]*0.7))

        # 左图：归一化得分
        colors1 = plt.cm.Blues(np.linspace(0.4, 0.8, len(factor_names)))
        bars1 = ax1.bar(factor_names, factor_scores, color=colors1,
                       edgecolor='black', linewidth=0.5)
        ax1.set_ylabel('Normalized score', fontsize=10)
        ax1.set_title(f'{student_name}: Factor scores', fontsize=11, fontweight='bold')
        ax1.set_ylim(0, 1)
        ax1.grid(axis='y', alpha=0.3)

        for bar, score in zip(bars1, factor_scores):
            height = bar.get_height()
            ax1.text(bar.get_x() + bar.get_width()/2., height + 0.02,
                    f'{score:.2f}', ha='center', va='bottom', fontsize=9)

        # 右图：加权贡献
        weighted_contributions = [s * w for s, w in zip(factor_scores, weights)]
        colors2 = plt.cm.Greens(np.linspace(0.4, 0.8, len(factor_names)))
        bars2 = ax2.bar(factor_names, weighted_contributions, color=colors2,
                       edgecolor='black', linewidth=0.5)
        ax2.set_ylabel('Weighted contribution', fontsize=10)
        ax2.set_title(f'{student_name}: Utility breakdown', fontsize=11, fontweight='bold')
        ax2.set_ylim(0, max(weights))
        ax2.grid(axis='y', alpha=0.3)

        for bar, contrib, weight in zip(bars2, weighted_contributions, weights):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + 0.01,
                    f'{contrib:.2f}\n(λ={weight:.2f})',
                    ha='center', va='bottom', fontsize=8)

        plt.tight_layout()
        return self._fig_to_markdown(fig, caption)

    # ========== Model 可视化 ==========

    def plot_time_budget(
        self,
        jobs: List[str],
        work_hours: List[float],
        commute_hours: List[float],
        recreation_hours: List[float],
        caption: str = ""
    ) -> str:
        """
        绘制时间预算堆叠柱状图

        Args:
            jobs: 工作名称
            work_hours: 工作时间
            commute_hours: 通勤时间
            recreation_hours: 娱乐时间
            caption: 图表标题
        """
        fig, ax = plt.subplots(figsize=(self.figsize[0], self.figsize[1]))

        x = np.arange(len(jobs))
        width = 0.6

        p1 = ax.bar(x, work_hours, width, label='Work hours',
                   color='#e74c3c', edgecolor='black', linewidth=0.5)
        p2 = ax.bar(x, commute_hours, width, bottom=work_hours,
                   label='Commute hours', color='#f39c12', edgecolor='black', linewidth=0.5)
        p3 = ax.bar(x, recreation_hours, width,
                   bottom=np.array(work_hours) + np.array(commute_hours),
                   label='Recreation hours', color='#27ae60', edgecolor='black', linewidth=0.5)

        ax.set_ylabel('Weekly hours', fontsize=11)
        ax.set_title(caption or 'Weekly time budget by job', fontsize=12, fontweight='bold')
        ax.set_xticks(x)
        ax.set_xticklabels(jobs, rotation=45, ha='right', fontsize=9)
        ax.legend(loc='upper right', fontsize=9)
        ax.grid(axis='y', alpha=0.3)

        plt.tight_layout()
        return self._fig_to_markdown(fig, caption)

    def plot_earnings_vs_time(
        self,
        jobs: List[str],
        earnings: List[float],
        total_time: List[float],
        caption: str = ""
    ) -> str:
        """
        绘制收入vs时间投入散点图

        Args:
            jobs: 工作名称
            earnings: 周收入
            total_time: 总时间投入（工作+通勤）
            caption: 图表标题
        """
        fig, ax = plt.subplots(figsize=(self.figsize[0], self.figsize[1]))

        colors = plt.cm.viridis(np.linspace(0, 1, len(jobs)))

        for i, (job, earn, time) in enumerate(zip(jobs, earnings, total_time)):
            ax.scatter(time, earn, s=200, c=[colors[i]],
                      edgecolor='black', linewidth=1.5, alpha=0.7, label=job)
            ax.text(time + 0.5, earn + 10, job, fontsize=9, ha='left')

        ax.set_xlabel('Total weekly time commitment (h)', fontsize=11)
        ax.set_ylabel('Weekly earnings (USD)', fontsize=11)
        ax.set_title(caption or 'Earnings vs time trade-off', fontsize=12, fontweight='bold')
        ax.grid(True, alpha=0.3)

        plt.tight_layout()
        return self._fig_to_markdown(fig, caption)


# ========== 辅助函数：从论文内容提取数据 ==========

def extract_sensitivity_data(content: str) -> Optional[Dict]:
    """从Sensitivity Analysis章节提取数据用于可视化"""
    # TODO: 实现从文本提取utility函数参数
    # 这里返回示例数据结构
    return None


def extract_results_data(content: str) -> Optional[Dict]:
    """从Results章节提取数据用于可视化"""
    # TODO: 实现从表格提取学生utility数据
    return None


# ========== 演示用例 ==========

if __name__ == '__main__':
    viz = PaperVisualizer()

    # 示例1: Sensitivity Analysis曲线
    lambda_E = np.linspace(0, 1, 100)
    utilities = {
        'J7 (Warehouse)': 0.16 + 0.84 * lambda_E,
        'J3 (Tutor)': 0.95 - 0.82 * lambda_E,
        'J6 (Analyst)': 0.83 - 0.55 * lambda_E,
    }

    md1 = viz.plot_utility_vs_weight(
        weight_name='λ_E',
        weight_values=lambda_E,
        utilities=utilities,
        current_weight=0.5,
        recommended_job='J7 (Warehouse)',
        caption='Utility sensitivity to earnings weight (Student Alex)'
    )

    print("Sensitivity Analysis图表已生成")
    print(f"Markdown长度: {len(md1)} 字符")

    # 示例2: Results对比柱状图
    students = ['Alex', 'Blake', 'Casey', 'Devon', 'Elliot',
                'Frankie', 'Greer', 'Hayden', 'Indigo', 'Jordan']
    utilities_list = [0.580, 0.720, 0.806, 0.768, 0.690,
                     0.794, 0.812, 0.715, 0.801, 0.698]
    jobs_list = ['J7', 'J7', 'J3', 'J3', 'J7',
                'J3', 'J3', 'J7', 'J3', 'J7']

    md2 = viz.plot_utility_comparison(
        student_names=students,
        utilities=utilities_list,
        jobs=jobs_list,
        caption='Total utility scores across ten validation cases'
    )

    print("Results对比图表已生成")
    print(f"Markdown长度: {len(md2)} 字符")
