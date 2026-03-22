# 导入函数库
from jqdata import *

def initialize(context):
    # 1. 设定基准
    set_benchmark('513130.XSHG')
    # 2. 开启动态复权
    set_option('use_real_price', True)
    
    # 3. 设定ETF交易成本
    set_order_cost(OrderCost(
        open_tax=0,
        close_tax=0,
        open_commission=0.0001,
        close_commission=0.0001,
        min_commission=0),
        type='fund'
    )
    
    # 4. 操作标的
    g.security = '513130.XSHG'
    
    # 5. 定时任务
    # 开盘卖出：使用 'open' 确保在开盘价成交
    run_daily(sell_op, time='open', reference_security=g.security)
    # 收盘买入：使用 '14:59' 代替 'close'，避免K线闭合问题，且能成交在收盘价附近
    run_daily(buy_op, time='close', reference_security=g.security)

def sell_op(context):
    """开盘卖出：增加严谨性检查"""
    s = g.security
    pos = context.portfolio.positions[s]
    
    # 【优化1】只有当确实持有可卖仓位时才下单，避免警告
    if pos.total_amount > 0 and pos.closeable_amount > 0:
        order_target(s, 0)
        log.info(f"【卖出】{s}，数量：{pos.closeable_amount}")

def buy_op(context):
    """收盘买入：增加上市日期检查"""
    s = g.security
    cash = context.portfolio.available_cash
    
    # 【优化2】检查标的在当前是否已经上市
    # 获取基金信息
    fund_info = get_security_info(s)
    # context.current_dt 是当前回测时间
    if fund_info and fund_info.start_date > context.current_dt.date():
        log.warning(f"标的 {s} 尚未上市，跳过买入")
        return
    
    if cash > 0:
        order_value(s, cash)
        log.info(f"【买入】{s}，金额：{cash:.2f}")