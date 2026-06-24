import yaml, pandas as pd
cfg = yaml.safe_load(open('config/scoring_l1_primary.cleaned.yaml','r',encoding='utf-8'))
keys = set(cfg['positives']['pb']['anchors_by_industry'].keys())
l1 = set(pd.read_csv('stock_industry_base.csv')['tdx_l1'].dropna().unique())
print("未匹配锚点：", sorted(k for k in keys - l1)[:50])
print("样例 L1：", list(sorted(l1))[:10])