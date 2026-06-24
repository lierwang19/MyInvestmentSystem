# MyInvestmentSystem 完整代码审核报告 (Code Audit Report)

根据《AGENTS.md》的审核规范，在对项目（包含 后端 API、数据库操作、ETL/打分脚本、定时任务、配置文件）进行完整性审查后，发现以下关键问题。按照严重程度（P0-P3）排序如下。

## P0 / 高风险

### [P0] `scripts/scoring/score_universe.py`

**问题**：打分引擎未处理 PB / PE 为负数或 ≤ 0 的情况。在计算分数时直接传入 `to_float()`，由于 `direction = "lower_better"`，负资产股 (PB < 0) 或连年亏损股 (PE < 0) 会落入 `value <= anchor` 的判断，从而直接获得满分 (100分)。

**为什么重要**：这会导致极差的垃圾股、濒临退市或暴雷的亏损企业被量化模型误判为“极度低估值”的好股票，严重污染策略备选池。

**建议修复**：在 `score_anchor_tiers` 中增加防线约束。读取 yaml 中的 `ignore_if_le_zero` 配置，并在判断中加上 `if v <= 0: return None` 彻底将其踢出打分范围或赋予最低分。

### [P0] `web/backend/api/transactions.py`

**问题**：过户费 (Transfer Fee) 计算遗漏。在 `create_transaction` 中，SQL 查询明确取出了四个费率：`b.commission_rate, b.min_commission, b.stamp_tax_rate, b.transfer_fee_rate`，但在随后的 Python 处理中，代码仅解包并使用了佣金和印花税，完全遗漏了 `transfer_fee_rate`，导致买卖双方费用均未计算“过户费”。

**为什么重要**：在 A 股实盘交易中，上海市场和近年新规存在双向收取的过户费。长期高频做 T 或换股，过户费累计不是小数目，漏算会导致系统可用资金（`cash_balance`）和账户净值与券商真实资产脱节。

**建议修复**：
从 `broker_row` 中解包出 `transfer_fee_rate`，设置合理的缺省值（如 `0.00001` 十万分之一），并在总 `fee` 计算中叠加上 `total_amt * transfer_fee_rate`，计入账户扣除逻辑。

## P1 / 重要风险

### [P1] `web/backend/api/analysis.py`

**问题**：由于使用 f-string 和手动循环拼接查询条件，存在潜在的 SQL 转义错误风险。例如 `ts_codes_str = ",".join(f"'{c}'" for c in ts_codes)`，以及 `mconn.execute(f"SELECT ... WHERE ts_code IN ({ts_codes_str})")`。

**为什么重要**：不符合项目级别的“不允许使用拼接 SQL”的审查基准规范。如果 `ts_code` 携带异常字符，将引发语法错误直接导致整个持仓大盘的分析接口瘫痪。

**建议修复**：重写为标准的 DBAPI 占位符结构：`placeholders = ",".join("?" for _ in ts_codes); mconn.execute(f"SELECT ... IN ({placeholders})", list(ts_codes))`。

### [P1] `config/scoring.yaml`

**问题**：行业的计算锚点未覆盖项目新版细分架构。文件底部的 `anchors.l2` 中枚举了上百个新版细分行业，打分逻辑依赖 `anchor_industry` 作为字典键寻找对比锚点。但在主要的 `pb.anchors_by_industry` 部分只有区区 17 个（例如 `公路铁路`、`水务` 等旧版），`pe_ttm` 只有不到一半覆盖。

**为什么重要**：如果一支股票处于未映射的行业类别中，打分将会退化 fallback 至全局默认值（PB=1.0, PE=8）。这种一刀切退化会导致高成长科技类股票被判定为奇高估值，从而失去评分体系对于行业区隔度的专业意义。

**建议修复**：在打分引擎实际上线跑全市场选股前，请由业务逻辑开发全量对齐新的 `L2` 细分行业名称，或者在 Python 引擎里增加 L2 找不到时向对应 L1 大类平滑 Fallback 的机制。

## P2 / 一般问题

### [P2] `web/backend/api/import_flow.py` SQL 事务与控制写法

**问题**：由于业务复杂逻辑需要内联嵌套事务，使用了纯手工的 `conn.execute(f"SAVEPOINT {sp}")`；但并未全部包裹在绝对可靠的 `try...finally` 块中。

**为什么重要**：如果 FastApi 接口处理途中遇到断网、非法关机或者 Python 进程级别的信号中断，有可能导致 `RELEASE SAVEPOINT` 丢失执行环节，进而死锁其他向 user_data 写入的动作。

**建议修复**：对于所有手动管理的 SAVEPOINT，严格引入 `contextlib` 或是纯语法层的 `try...except...finally` 拦截做事务退出操作。

---

## 总结与建议工作流

作为代码审核员的角色，我已完成了全系统的审计并指出了阻碍功能一致性的首要缺陷。请主代码库所有者（Claude Code 实操端）针对此《审核报告》开启后续修复：
1. **立即介入修复 P0 逻辑死角**（特别是由于缺项导致的垃圾股打分错误，此逻辑污染性强）。
2. **处理过户费业务遗漏**，保障资金闭环运算的纯净。
3. **全局修复 SQL f-string 拼接规范漏洞**，消除隐蔽报错引发的应用崩溃。
