# 1. 导入所需依赖库
import json
import os
import logging
import numpy as np
import pandas as pd
from scipy.optimize import minimize
from sklearn.covariance import LedoitWolf

# --------------------------
# 2. 核心计算函数定义
# --------------------------
def calc_cov_matrix(returns_df, annualize=True):
    """
    计算资产收益率的协方差矩阵
    :param returns_df: pd.DataFrame，日频收益率数据，列=资产名，行=日期
    :param annualize: 是否年化，日频数据默认乘以252个交易日
    :return: 协方差矩阵 np.array
    """
    lw = LedoitWolf()
    lw.fit(returns_df.values)
    cov_matrix = lw.covariance_
    if annualize:
        cov_matrix = cov_matrix * 252
    return cov_matrix

def portfolio_volatility(weights, cov_matrix):
    """计算组合波动率σ_p"""
    return np.sqrt(np.dot(weights.T, np.dot(cov_matrix, weights)))

def marginal_risk_contribution(weights, cov_matrix):
    """计算边际风险贡献MRC"""
    port_vol = portfolio_volatility(weights, cov_matrix)
    sigma_w = np.dot(cov_matrix, weights)
    return sigma_w / port_vol

def total_risk_contribution(weights, cov_matrix):
    """计算总风险贡献TRC"""
    mrc = marginal_risk_contribution(weights, cov_matrix)
    return weights * mrc

def risk_parity_objective(weights, params):
    """风险平价优化目标函数：最小化TRC与目标风险预算的差值平方和"""
    cov_matrix = params['cov_matrix']
    risk_budget = params['risk_budget']
    
    trc = total_risk_contribution(weights, cov_matrix)
    port_vol = portfolio_volatility(weights, cov_matrix)
    target_trc = port_vol * risk_budget  # 目标风险贡献
    
    return np.sum((trc - target_trc) ** 2)

def solve_risk_parity(cov_matrix, risk_budget=None, init_weights=None):
    """
    求解风险平价最优权重
    :param cov_matrix: 协方差矩阵
    :param risk_budget: 自定义风险权重，数组长度=资产数量，和为1；默认等风险
    :param init_weights: 优化初始权重，默认等权重
    :return: 优化结果对象，最优权重存储在result.x中
    """
    n_assets = cov_matrix.shape[0]
    
    # 初始化参数
    risk_budget = np.ones(n_assets)/n_assets if risk_budget is None else risk_budget
    init_weights = np.ones(n_assets)/n_assets if init_weights is None else init_weights
    
    # 约束条件：权重和为1
    constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
    # 边界条件：不做空、不加杠杆
    bounds = tuple((0, 1) for _ in range(n_assets))
    
    # 打包优化参数
    params = {'cov_matrix': cov_matrix, 'risk_budget': risk_budget}
    
    # 求解带约束的非线性优化
    result = minimize(
        fun=risk_parity_objective,
        x0=init_weights,
        args=params,
        bounds=bounds,
        constraints=constraints,
        method='SLSQP',
        # 核心参数：降低tol=收敛容忍度，提高maxiter=最大迭代
        options={
            'tol': 1e-10,
            'maxiter': 10000,
            'disp': False
        }
    )
    return result

# --------------------------
# 3. 数据读取与处理函数
# --------------------------
def load_config(config_path):
    """读取并解析config.json配置文件"""
    with open(config_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def read_single_asset(asset_info, data_dir, start_date, end_date):
    """
    读取单个资产的CSV数据并计算日收益率
    :param asset_info: 资产信息字典（含name, code, type）
    :param data_dir: 数据文件所在目录
    :param start_date: 开始日期
    :param end_date: 结束日期
    :return: 资产日收益率Series（index为日期，name为资产名）
    """
    code = asset_info['code']
    name = asset_info['name']
    file_path = os.path.join(data_dir, f"{code}.csv")
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"资产 {name} 的数据文件不存在：{file_path}")
    
    # 读取CSV并处理日期
    df = pd.read_csv(file_path, encoding='utf-8')
    df['净值日期'] = pd.to_datetime(df['净值日期'])
    df = df.set_index('净值日期').sort_index()
    
    # 筛选日期范围
    df = df.loc[start_date:end_date]
    if df.empty:
        raise ValueError(f"资产 {name} 在 {start_date} 至 {end_date} 期间无数据")
    
    # 使用累计净值计算日收益率
    if '累计净值' not in df.columns:
        raise ValueError(f"资产 {name} 的数据文件缺少“累计净值”列")
    
    returns = df['累计净值'].pct_change().dropna()
    returns.name = name
    return returns

def load_all_assets(assets, data_dir, start_date, end_date):
    """
    加载所有资产数据并合并为收益率DataFrame
    :return: 对齐日期后的收益率DataFrame
    """
    returns_list = []
    for asset in assets:
        try:
            returns = read_single_asset(asset, data_dir, start_date, end_date)
            returns_list.append(returns)
            logging.info(f"成功加载资产：{asset['name']}，数据量：{len(returns)}")
        except Exception as e:
            logging.warning(f"跳过资产 {asset['name']}，原因：{str(e)}")
    
    if not returns_list:
        raise ValueError("没有成功加载任何资产数据")
    
    # 合并所有资产收益率（内连接，仅保留所有资产都有数据的日期）
    returns_df = pd.concat(returns_list, axis=1, join='inner')
    logging.info(f"合并后数据日期范围：{returns_df.index[0]} 至 {returns_df.index[-1]}，共 {len(returns_df)} 个交易日")
    return returns_df

# --------------------------
# 4. 主程序
# --------------------------
if __name__ == '__main__':
    # 获取脚本所在目录
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 配置路径
    config_path = os.path.join(script_dir, 'config.json')
    data_dir = os.path.join(script_dir, 'data')
    output_dir = os.path.join(script_dir, 'output')
    
    # 创建output目录（若不存在）
    os.makedirs(output_dir, exist_ok=True)
    
    # 配置日志
    log_file = os.path.join(output_dir, 'risk_parity.log')
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    
    try:
        logging.info("="*60)
        logging.info("风险平价计算程序启动")
        logging.info("="*60)
        
        # 1. 读取配置
        logging.info("正在读取配置文件...")
        config = load_config(config_path)
        start_time = config['start_time']
        end_time = config['end_time']
        assets = config['assets']
        logging.info(f"配置加载成功：时间范围 {start_time} 至 {end_time}，共 {len(assets)} 个资产")
        
        # 2. 加载并处理数据
        logging.info("\n正在加载资产数据...")
        returns_df = load_all_assets(assets, data_dir, start_time, end_time)
        
        # 3. 提取风险预算（按资产名称对齐）
        asset_names = returns_df.columns.tolist()
        risk_budget = []
        for name in asset_names:
            asset_info = next(a for a in assets if a['name'] == name)
            risk_budget.append(asset_info['risk_weight'])
        risk_budget = np.array(risk_budget)
        
        # 校验风险预算和为1
        if not np.isclose(risk_budget.sum(), 1.0):
            logging.warning(f"风险预算和不为1（当前和：{risk_budget.sum():.6f}），已自动归一化")
            risk_budget = risk_budget / risk_budget.sum()
        
        # 4. 计算协方差矩阵
        logging.info("\n正在计算协方差矩阵...")
        cov_matrix = calc_cov_matrix(returns_df, annualize=True)
        
        # 5. 求解风险平价最优权重
        logging.info("正在求解最优权重...")
        opt_result = solve_risk_parity(cov_matrix, risk_budget=risk_budget)
        if not opt_result.success:
            raise RuntimeError(f"优化失败：{opt_result.message}")
        opt_weights = opt_result.x
        
        # 6. 计算结果指标
        port_vol = portfolio_volatility(opt_weights, cov_matrix)
        trc = total_risk_contribution(opt_weights, cov_matrix)
        trc_ratio = trc / port_vol
        
        # 7. 输出结果到日志
        logging.info("\n" + "="*60)
        logging.info("【风险平价计算结果】")
        logging.info("="*60)
        
        logging.info("\n1. 最优资产权重：")
        for name, weight in zip(asset_names, opt_weights):
            logging.info(f"   {name:40s} {weight:8.4%}")
        
        logging.info("\n2. 风险贡献校验：")
        for name, ratio, target in zip(asset_names, trc_ratio, risk_budget):
            logging.info(f"   {name:40s} 实际：{ratio:8.4%} | 目标：{target:8.4%} | 偏差：{abs(ratio-target):.6%}")
        
        logging.info(f"\n3. 组合年化波动率：{port_vol:.4%}")
        logging.info("\n" + "="*60)
        logging.info("计算完成！结果已保存至日志文件")
        logging.info("="*60)
        
    except Exception as e:
        logging.error(f"\n程序出错：{str(e)}", exc_info=True)
        raise