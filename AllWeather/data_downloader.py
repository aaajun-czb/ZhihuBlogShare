import json
import os
import akshare as ak
from datetime import datetime

import akshare_proxy_patch
akshare_proxy_patch.install_patch(
    "101.201.173.125",
    retry=30,
)

def main():
    # 1. 读取配置文件
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    # 2. 创建数据保存目录
    data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    # 3. 获取今日日期 (格式: YYYYMMDD)
    today = datetime.today().strftime('%Y%m%d')
    
    # 4. 遍历标的并下载数据
    assets = config.get('assets', [])
    for asset in assets:
        asset_name = asset['name']
        asset_code = asset['code']
        asset_type = asset['type']
        
        print(f"正在下载 [{asset_type}] {asset_name} ({asset_code}) ...")
        
        try:
            if asset_type == 'OF':
                # 下载开放式基金 (累计净值走势)
                df = ak.fund_open_fund_info_em(
                    symbol=asset_code,
                    indicator="累计净值走势"
                )
            elif asset_type in ['ETF', 'LOF']:
                # 下载场内交易基金 (历史数据)
                df = ak.fund_etf_fund_info_em(
                    fund=asset_code,
                    start_date="20000101",  # 足够早的开始时间
                    end_date=today
                )
            else:
                print(f"未知标的类型: {asset_type}, 跳过 {asset_name}")
                continue
            
            # 5. 保存为 CSV 文件
            # 使用基金代码作为文件名，避免特殊字符问题
            save_path = os.path.join(data_dir, f"{asset_code}.csv")
            df.to_csv(save_path, index=False, encoding='utf-8-sig')
            print(f"成功保存至: {save_path}\n")
            
        except Exception as e:
            print(f"下载失败 [{asset_name}]: {str(e)}\n")


if __name__ == "__main__":
    main()