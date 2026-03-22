# 导入函数库
from jqdata import *

# 初始化函数
def initialize(context):
    set_benchmark('000300.XSHG')
    set_option('use_real_price', True)
    log.info('初始函数开始运行且全局只运行一次')

    ### 融资融券相关设定
    set_subportfolios([SubPortfolioConfig(cash=context.portfolio.cash, type='stock_margin')])
    
    # 全局变量存储两融参数
    g.margincash_interest_rate = 0.08    # 融资利率8%
    g.margincash_margin_rate = 1.0       # 融资保证金100%
    g.marginsec_interest_rate = 0.10     # 融券利率10%
    g.marginsec_margin_rate = 1.0        # 融券保证金100%
    g.capital_efficiency = 0.9          # 资金使用效率90%
    
    # 应用两融参数
    set_option('margincash_interest_rate', g.margincash_interest_rate)
    set_option('margincash_margin_rate', g.margincash_margin_rate)
    set_option('marginsec_interest_rate', g.marginsec_interest_rate)
    set_option('marginsec_margin_rate', g.marginsec_margin_rate)

    # 全局变量：策略逻辑相关
    g.etf = '510300.XSHG'
    g.yesterday_short_amount = 0  # 记录前一日的融券数量
    g.is_first_day = True         # 标记是否为第一天

    ### 定时运行函数（核心调整：拆分还券和融券操作，早盘先还券）
    run_daily(buy_and_refund_etf, time='open', reference_security=g.etf)      # 9:30买入+立即还旧券
    run_daily(short_sell_new_position, time='close', reference_security=g.etf)# 14:55融新券做空
    run_daily(check_account_status, time='after_close', reference_security=g.etf) # 收盘后核查


## 早盘：买入ETF + 立即归还前一日融券负债（核心优化：买完就还，释放保证金）
def buy_and_refund_etf(context):
    log.info('函数运行时间(buy_and_refund_etf)：'+str(context.current_dt.time()))
    etf = g.etf
    sub_portfolio = context.portfolio.subportfolios[0]
    
    # 第一天：不买入、不还券，只准备尾盘融券
    if g.is_first_day:
        log.info('第一天，不买入ETF，不还券')
        return
    
    # 非第一天：买入数量=前一日融券数量
    buy_amount = g.yesterday_short_amount
    if buy_amount <= 0:
        log.info('前一日无融券数量，无需买入还券')
        return
    
    # 1. 计算买入所需资金，确保足够
    current_price = get_current_data()[etf].last_price
    need_cash = buy_amount * current_price * 1.01  # 仅留1%预防价格波动（足够ETF）
    available_cash = sub_portfolio.available_cash
    
    if available_cash < need_cash:
        log.info(f'可用资金不足，需要{need_cash:.2f}元，实际可用{available_cash:.2f}元，放弃买入还券')
        return
    
    # 2. 执行买入
    log.info(f'早盘买入{etf}，数量={buy_amount}股（用于归还前一日融券）')
    order_result = order(etf, buy_amount, side='long')
    if not order_result or order_result.filled != buy_amount:
        log.info(f'买入失败/成交数量不足，实际成交{order_result.filled if order_result else 0}股')
        return
    
    # 3. 买入后立即还券（核心优化：不再等尾盘，早盘就释放保证金）
    short_position = sub_portfolio.short_positions.get(etf, None)
    if short_position and short_position.total_amount >= buy_amount:
        log.info(f'买入后立即还券，归还前一日融券负债{buy_amount}股')
        marginsec_direct_refund(etf, buy_amount)
    else:
        log.info(f'无法还券：融券负债={short_position.total_amount if short_position else 0}股，刚买入{buy_amount}股')


## 尾盘：动态计算融券数量，融新券做空
def short_sell_new_position(context):
    log.info('函数运行时间(short_sell_new_position)：'+str(context.current_dt.time()))
    etf = g.etf
    sub_portfolio = context.portfolio.subportfolios[0]
    current_price = get_current_data()[etf].last_price
    
    # 1. 动态计算融券数量（资金效率95%）
    available_margin = sub_portfolio.available_margin
    if available_margin <= 0:
        log.info('可用保证金为0，不融券')
        g.yesterday_short_amount = 0
        return
    
    # 公式：融券数量 = (可用保证金 × 资金效率) / (当前价格 × 融券保证金比例)
    theoretical_amount = (available_margin * g.capital_efficiency) / (current_price * g.marginsec_margin_rate)
    short_amount = int(theoretical_amount // 100 * 100)  # 取整到100的整数倍
    
    if short_amount < 100:
        log.info(f'计算出的融券数量不足100股（理论={theoretical_amount:.2f}股），不融券')
        g.yesterday_short_amount = 0
        return
    
    # 2. 检查保证金是否足够
    need_margin = short_amount * current_price * g.marginsec_margin_rate
    if available_margin < need_margin:
        log.info(f'保证金不足，需要{need_margin:.2f}元，实际可用{available_margin:.2f}元，不融券')
        g.yesterday_short_amount = 0
        return
    
    # 3. 执行融券卖出（核心：仅做空，无对冲）
    log.info(f'尾盘融券卖出{etf}，数量={short_amount}股（资金效率{g.capital_efficiency*100:.0f}%）')
    marginsec_open(etf, short_amount)
    g.yesterday_short_amount = short_amount  # 记录今日融券数量，供明日还券
    
    # 第一天结束后标记为False
    g.is_first_day = False


## 收盘后：核查账户状态（重点看融券空仓和资金效率）
def check_account_status(context):
    p = context.portfolio.subportfolios[0]
    etf = g.etf
    
    # 获取持仓数据
    long_amount = p.positions.get(etf, None).total_amount if p.positions.get(etf, None) else 0
    short_amount = p.short_positions.get(etf, None).total_amount if p.short_positions.get(etf, None) else 0
    
    # 计算实际资金使用效率
    current_price = get_current_data()[etf].last_price
    used_margin = short_amount * current_price * g.marginsec_margin_rate
    total_margin = p.available_margin + used_margin  # 总保证金=可用+已用
    actual_efficiency = used_margin / total_margin if total_margin > 0 else 0
    
    log.info('- - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -')
    log.info('收盘后账户状态（做空沪深300）：')
    log.info(f'总资产：{p.total_value:.2f}元 | 净资产：{p.net_value:.2f}元')
    log.info(f'ETF多仓数量：{long_amount}股（理想值0）')
    log.info(f'ETF融券空仓数量：{short_amount}股（核心做空头寸）')
    log.info(f'实际资金使用效率：{actual_efficiency*100:.2f}%（目标95%）')
    log.info('##############################################################')