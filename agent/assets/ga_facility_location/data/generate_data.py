"""
生成 10 个需求点坐标，保存为 CSV 文件
"""
import numpy as np
import pandas as pd

np.random.seed(42)  # 固定随机种子，结果可复现
n_demands = 10
demands = np.random.uniform(0, 10, size=(n_demands, 2))

df = pd.DataFrame(demands, columns=['x', 'y'])
df.index.name = 'demand_id'
df.to_csv('../data/demand_points.csv')
print("需求点坐标已保存到 data/demand_points.csv")
print(df)
