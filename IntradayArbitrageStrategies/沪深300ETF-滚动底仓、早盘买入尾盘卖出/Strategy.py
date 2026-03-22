# 导入函数库
from jqdata import *

def initialize(context):
    # 1. 设定基准为沪深300指数
    set_benchmark('000300.XSHG')
    # 2. 开启动态复权模式
    set_option('use_real_price', True)
    # 3. 全局变量设置
    g.security = '510300.XSHG'          # 标的：沪深300ETF
    g.base_position_ratio = 0.49          # 第一天底仓资金占比（49%，留51%做T）
    g.t_funds_ratio = 1.0                  # T仓用满当日可用资金
    g.base_position = 0                    # 底仓股数（自动计算）
    g.today_buy_amount = 0                 # 记录当日买入的T仓数量
    g.initial_cash = context.portfolio.starting_cash  # 记录初始资金
    g.is_first_day = True                  # 【核心修正】标记是否为第一天
    
    # 4. ETF交易费用设置
    set_order_cost(
        OrderCost(
            close_tax=0.0,          # ETF卖出无印花税
            open_commission=0.0002, # 买入佣金：万分之二
            close_commission=0.0002,# 卖出佣金：万分之二
            min_commission=0.1       # 最低佣金
        ),
        type='fund'
    )
    
    # 5. 定时运行函数
    run_daily(build_base_position, time='09:25', reference_security=g.security)  # 集合竞价后建底仓（仅第一天）
    run_daily(open_trade, time='09:30', reference_security=g.security)            # 开盘买T仓（第二天开始）
    run_daily(close_trade, time='14:55', reference_security=g.security)           # 收盘卖T仓
    run_daily(after_market_close, time='after_close', reference_security=g.security)  # 收盘日志
    run_daily(reset_daily_var, time='before_open', reference_security=g.security)  # 每日重置变量

def reset_daily_var(context):
    """每日开盘前重置当日买入的T仓数量"""
    g.today_buy_amount = 0

def build_base_position(context):
    """仅第一天建立底仓：用初始资金的49%买底仓，不做T"""
    current_hold = context.portfolio.positions[g.security].total_amount
    if current_hold == 0:
        # 获取前一日收盘价作为底仓买入价参考
        pre_close = get_bars(g.security, count=1, unit='1d', fields=['close'])[-1]['close']
        # 计算底仓股数：初始资金*0.49 / 价格，取整到100的整数倍
        base_cash = g.initial_cash * g.base_position_ratio
        g.base_position = int(base_cash / pre_close / 100) * 100
        if g.base_position <= 0:
            log.error("初始资金不足，无法建立底仓")
            return
        # 执行底仓买入
        log.info(f"【第一天】仅建立底仓：买入 {g.base_position} 股，资金：{base_cash:.2f}元（剩余资金留作T仓）")
        order(g.security, g.base_position)

def open_trade(context):
    """开盘买T仓：第一天不买，第二天开始用满可用资金，且不超过底仓数量"""
    # 1. 【核心修正】检查是否是第一天：第一天不买T仓
    if g.is_first_day:
        log.info("【第一天】不操作T仓，仅持有底仓")
        return
    
    # 2. 检查底仓是否已建立
    if g.base_position == 0:
        log.info("底仓未建立，暂停T仓买入")
        return
    
    # 3. 用满当日可用资金
    available_cash = context.portfolio.available_cash
    if available_cash < 1000:
        log.info("可用资金不足，暂停T仓买入")
        return
    
    # 4. 获取当前开盘价
    current_price = get_current_data()[g.security].day_open
    if not current_price:
        current_price = get_bars(g.security, count=1, unit='1m', fields=['close'])[-1]['close']
    
    # 5. 计算T仓买入数量：优先用满资金，但不超过底仓数量（确保能全部卖出）
    max_t_amount = int(available_cash / current_price / 100) * 100  # 资金允许的最大数量
    buy_amount = min(max_t_amount, g.base_position)  # 不超过底仓数量
    if buy_amount <= 0:
        log.info("可买入数量不足，暂停T仓买入")
        return
    
    # 6. 执行买入，并记录当日买入量
    log.info(f"【T仓买入】{buy_amount} 股，价格：{current_price:.2f}，资金：{buy_amount*current_price:.2f}元")
    order_result = order(g.security, buy_amount)
    if order_result is not None:
        g.today_buy_amount += buy_amount

def close_trade(context):
    """收盘前卖出当日买入的全部T仓"""
    # 【核心修正】第一天不操作卖出
    if g.is_first_day:
        log.info("【第一天】不操作T仓卖出")
        return
    
    if g.today_buy_amount <= 0:
        log.info("当日无T仓买入，无需卖出")
        return
    
    # 检查可卖出数量（优先用底仓份额卖出）
    current_hold = context.portfolio.positions[g.security].closeable_amount
    sell_amount = min(g.today_buy_amount, current_hold)
    if sell_amount > 0:
        current_price = get_bars(g.security, count=1, unit='1m', fields=['close'])[-1]['close']
        log.info(f"【T仓卖出】{sell_amount} 股，价格：{current_price:.2f}")
        order(g.security, -sell_amount)
        g.today_buy_amount -= sell_amount
    else:
        log.info("无可用份额卖出T仓")

def after_market_close(context):
    """收盘后日志：记录资金利用率和持仓，并更新第一天标记"""
    total_value = context.portfolio.total_value
    cash_used = g.initial_cash - context.portfolio.available_cash
    utilization = (cash_used / g.initial_cash) * 100 if g.initial_cash > 0 else 0
    total_hold = context.portfolio.positions[g.security].total_amount
    closeable_hold = context.portfolio.positions[g.security].closeable_amount
    
    log.info(f"【资金利用率】{utilization:.2f}%，总市值：{total_value:.2f}元")
    log.info(f"【持仓情况】总持仓：{total_hold} 股（底仓：{g.base_position} 股，可卖：{closeable_hold} 股）")
    
    trades = get_trades()
    if trades:
        for trade in trades.values():
            direction = "买入" if trade.amount > 0 else "卖出"
            log.info(f"【成交记录】{trade.security} {direction} {abs(trade.amount)} 股，价格：{trade.price:.2f}")
    else:
        log.info("【成交记录】当日无成交")
    
    # 【核心修正】第一天结束后，更新标记为False
    if g.is_first_day:
        g.is_first_day = False
        log.info("【第一天结束】明天开始正常进行T+0操作")
    
    log.info("##############################################################")