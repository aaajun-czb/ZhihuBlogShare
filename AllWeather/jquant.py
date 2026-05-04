# 导入函数库
from jqdata import *
import datetime

def initialize(context):
    # 设定基准为沪深300
    set_benchmark('000300.XSHG')
    # 开启动态复权模式(真实价格)
    set_option('use_real_price', True)
    log.info('初始函数开始运行且全局只运行一次')

    # 设置股票/ETF/基金的手续费（开仓/平仓均为万分之0.5，无印花税）
    set_order_cost(OrderCost(open_tax=0, close_tax=0, 
                             open_commission=0.00005, close_commission=0.00005,
                             min_commission=0), 
                   type='fund')  # 'fund'覆盖ETF/LOF/基金等

    ### 初始化标记：是否为第一个交易日 ###
    g.first_trading_day = True

    ### 获取511260的上市日期，用于后续判断 ###
    g.security_511260 = '511260.XSHG'
    g.info_511260 = get_security_info(g.security_511260)
    if g.info_511260:
        g.list_date_511260 = g.info_511260.start_date
        log.info(f"511260 上市日期确认: {g.list_date_511260}")
    else:
        g.list_date_511260 = datetime.date(2017, 8, 24)
        log.warning("无法获取511260上市日期，使用默认2017-08-24")

    ### 融资融券账户核心设定 ###
    set_subportfolios([SubPortfolioConfig(cash=context.portfolio.cash, type='stock_margin')])
    set_option('margincash_interest_rate', 0.03)  # 融资利率：年化3%
    set_option('margincash_margin_rate', 0.25)      # 融资保证金比率

    # 【新增】定义债券列表和保证金比例
    g.bond_securities = ['511010.XSHG', '511260.XSHG']  # 债券类标的
    g.margin_rate = 0.25 # 融资保证金比例（与 set_option 设置一致）
    
    ### 基础组合配置 ###
    g.base_portfolio = {
        '510880.XSHG': 0.077,   # 上证红利
        '513500.XSHG': 0.045,   # 标普500
        '511010.XSHG': 0.379,   # 中久期中债 (基础)
        '511260.XSHG': 0.386,   # 长久期中债 (待判断)
        '518880.XSHG': 0.074,   # 黄金
        '160216.XSHE': 0.039    # 大宗商品
    }
    g.target_leverage = 3  # 目标总杠杆倍数

    ### 定时运行函数 ###
    run_daily(before_market_open, time='before_open', reference_security='000300.XSHG')
    run_daily(market_open, time='open', reference_security='000300.XSHG')
    run_daily(after_market_close, time='after_close', reference_security='000300.XSHG')

### 辅助函数：动态获取当前有效的投资组合 ###
def get_current_portfolio(context):
    current_date = context.current_dt.date()
    current_weights = g.base_portfolio.copy()
    
    if current_date < g.list_date_511260:
        log.info(f"当前日期 {current_date} 早于511260上市日，使用511010替代")
        weight_511260 = current_weights.pop(g.security_511260, 0)
        if '511010.XSHG' in current_weights:
            current_weights['511010.XSHG'] += weight_511260
        else:
            current_weights['511010.XSHG'] = weight_511260
    else:
        log.info(f"当前日期 {current_date} 晚于/等于511260上市日，使用原组合")
    
    return current_weights

### 辅助函数：判断季度末最后一个交易日 ###
def is_quarter_end(context):
    current_date = context.current_dt.date()
    month = current_date.month
    year = current_date.year
    
    if month not in [3, 6, 9, 12]:
        return False
    
    month_start = datetime.date(year, month, 1)
    next_month_start = datetime.date(year, month + 1, 1) if month < 12 else datetime.date(year + 1, 1, 1)
    month_end = next_month_start - datetime.timedelta(days=1)
    month_trade_days = get_trade_days(start_date=month_start, end_date=month_end)
    
    return len(month_trade_days) > 0 and current_date == month_trade_days[-1]

def before_market_open(context):
    log.info(f'函数运行时间(before_market_open)：{context.current_dt.time()}')

def market_open(context):
    if not (g.first_trading_day or is_quarter_end(context)):
        log.info("非第一天且非季度末交易日，不进行操作")
        return

    sub_pf = context.portfolio.subportfolios[0]
    net_value = sub_pf.net_value
    target_total = net_value * g.target_leverage

    # 获取动态投资组合
    current_weights = get_current_portfolio(context)

    if is_quarter_end(context):
        log.info("=== 季度末交易日，启动微调再平衡 ===")
    else:
        log.info("=== 交易第一天，启动初始建仓 ===")

    log.info(f"当前净资产: {net_value:.2f} | 目标总资产(杠杆{g.target_leverage}倍): {target_total:.2f}")

    # 计算所有标的的目标市值和当前市值
    positions = {}
    for sec, w in current_weights.items():
        target_val = target_total * w
        current_val = sub_pf.long_positions[sec].value if sec in sub_pf.long_positions else 0.0
        # 获取当前价格，用于计算最小交易单位所需金额
        price = get_current_data()[sec].day_open  # 假设按开盘价估算
        min_value = price * 100 if price else float('inf')
        positions[sec] = {
            'target': target_val,
            'current': current_val,
            'min_value': min_value
        }

    # ---------- 第一步：卖出超配的仓位（释放保证金）----------
    for sec, info in positions.items():
        if info['current'] > info['target']:
            order_target_value(sec, info['target'], side='long')
            log.info(f"卖出超配 {sec} 至目标市值 {info['target']:.2f}")

    # ---------- 第二步：买入非债券标的（优先）----------
    non_bonds = [s for s in positions if s not in g.bond_securities and positions[s]['current'] < positions[s]['target']]
    bonds = [s for s in positions if s in g.bond_securities and positions[s]['current'] < positions[s]['target']]

    buy_list = non_bonds + bonds  # 非债券优先

    for sec in buy_list:
        available_margin = sub_pf.available_margin
        if available_margin <= 0:
            log.warning("可用保证金不足，停止买入")
            break

        info = positions[sec]
        need_value = info['target'] - info['current']
        if need_value <= 0:
            continue

        # 根据可用保证金计算最大可买入市值
        max_buy_value = available_margin / g.margin_rate
        # 实际打算买入的金额（取 need 和 max 的较小值）
        actual_buy = min(need_value, max_buy_value)

        # 检查是否能达到最小交易单位（100股）
        if actual_buy < info['min_value']:
            log.info(f"{sec} 实际可买金额 {actual_buy:.2f} < 最小单位 {info['min_value']:.2f}，放弃")
            continue

        # 买入：将当前市值提升 actual_buy
        new_target = info['current'] + actual_buy
        order_target_value(sec, new_target, side='long')
        log.info(f"买入 {sec} 当前 {info['current']:.2f} -> 新目标 {new_target:.2f}（原目标 {info['target']:.2f}）")

    # 关闭“第一天”标记
    if g.first_trading_day:
        g.first_trading_day = False
        log.info("=== 初始建仓完成 ===")

def after_market_close(context):
    sub_pf = context.portfolio.subportfolios[0]
    log.info('--- 融资融券账户收盘信息 ---')
    log.info(f'总资产: {sub_pf.total_value:.2f}')
    log.info(f'净资产: {sub_pf.net_value:.2f}')
    log.info(f'总负债: {sub_pf.total_liability:.2f}')
    log.info(f'融资负债: {sub_pf.cash_liability:.2f}')
    log.info(f'可用保证金: {sub_pf.available_margin:.2f}')
    log.info(f'维持担保比例: {sub_pf.maintenance_margin_rate:.2f}')
    log.info('##############################')