# QA Checklist

本文件用于沉淀 MyInvestmentSystem 的固定 QA 回归流程。

目标：
- 每次重建后快速确认核心链路可用
- 每次发版前补齐关键人工检查
- 避免每轮 QA 都从零开始想“要测什么”

---

## L1 冒烟

适用场景：
- Docker 重建后
- 合并核心改动后
- 每日开发收尾快速自检

执行命令：

```bash
python3 scripts/tools/qa_smoke.py
```

当前脚本覆盖：
- 前端首页可打开
- `GET /api/accounts-db`
- `GET /api/analysis/holdings/A-02`
- `GET /api/analysis/stock-pool`
- `GET /api/market/current`
- `GET /api/system/status`
- `GET /api/quotes/status`
- `POST /api/accounts-db`
- `POST /api/upload/holdings`
- `POST /api/import/flow`
- `POST /api/screenshot/import/{account_id}`
- 自动清理临时测试数据

通过标准：
- 脚本输出 `[done] L1 smoke QA passed`

---

## L2 核心业务

适用场景：
- 准备发版
- 涉及交易、导入、账户、策略逻辑改动后

建议检查项：
- 券商新增、修改、删除
- 账户新增、修改、删除
- 资金流水新增、删除后，账户资金口径正确
- 自选股新增、修改、删除
- 持仓分析页、账户总览页、优选池页首个命中和重复命中性能
- 策略手动运行一次，核对 `signals` / `decisions`
- 短区间回测运行一次并删除结果
- `upload/incon` 非法输入返回 `400`
- `screenshot/parse` 非法图片返回 `400`

---

## L3 深水区

适用场景：
- 大改版前
- 每周一次深度回归
- 数据链路或调度链路有调整时

建议检查项：
- 真实券商流水样本导入
- 真实持仓文件导入
- 真实截图 OCR 识别质量
- ETL / sync / HK price / industry sync 重操作
- `scheduler` / `drip sync` 长时间运行稳定性
- 冷启动性能与缓存命中后性能对比

---

## 人工补充

脚本无法完全替代人工判断，以下内容建议人工确认：
- 页面是否有空白区、错位、明显样式回退
- 表格和图表是否出现空数据但无提示
- 错误提示是否是用户可理解的中文
- 导入结果是否符合真实业务预期，而不只是“接口返回 200”
- 关键日志中是否出现新的 traceback 或重复 warning

---

## 已知说明

- 当前环境下宿主机 `127.0.0.1:8001` 端口映射偶发有瞬时抖动。
- `scripts/tools/qa_smoke.py` 为了避开这个噪声，后端检查默认走 `invest_backend` 容器内直连。
- 如果容器名变化，可用：

```bash
python3 scripts/tools/qa_smoke.py --backend-container <container_name>
```

---

## 推荐使用方式

日常：

```bash
python3 scripts/tools/qa_smoke.py
```

发版前：
1. 先跑 `python3 scripts/tools/qa_smoke.py`
2. 再按本文的 L2 清单补人工检查
3. 若涉及 ETL / 调度 / OCR / 导入解析，补跑 L3 对应项
