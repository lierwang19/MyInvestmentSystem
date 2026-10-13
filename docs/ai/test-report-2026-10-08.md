# 前后端自动化测试与 Claude Code 修复交接

日期：2026-10-08（America/Los_Angeles）  
基线：`aa85de7`，测试对象为当前工作区，含此前未提交的可靠性补丁。  
本轮仅添加测试、测试工具配置和本报告；业务实现留给 Claude Code 修改。

## 结果

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端完整 pytest 测试集 | 160 项：149 通过、11 失败、0 跳过、0 测试错误 | `test-results/backend-tests.log`、`backend-tests.xml` |
| 前端完整 Vitest 测试集 | 21 项：19 通过、2 失败 | `test-results/frontend-tests.log`、`frontend-tests.xml` |
| 前端 TypeScript 检查及生产构建 | `vue-tsc -b && vite build` 通过 | `test-results/frontend-build.log` |
| 后端行覆盖率 | 31.64%，范围为 web/backend、ETL、打分、策略 | `test-results/backend-coverage.xml` |
| 前端行覆盖率 | 工具报告 1.08%，含所有 TS/Vue 源文件；多数页面未被测试访问 | `web/frontend/coverage/coverage-summary.json`、`coverage/index.html` |
| 差异格式检查 | `git diff --check` 通过 | 本轮命令执行结果 |

原有后端 137 项与前端 19 项测试均通过。新增 12 项真实 API 集成测试通过；
新增的 11 项后端验收场景和 2 项前端组件验收场景均失败，暴露了原有测试未覆盖的问题。
失败用例没有标记为 xfail，后续修复以断言通过为验收。

后端采用 Python 3.11.15、pytest 8.4.2；前端使用 Vitest 3.2.4。
API 集成测试使用真实 FastAPI 主应用、Router、模型与鉴权中间件，通过 ASGI
TestClient 请求临时 SQLite 数据；不启动 lifespan 中的 poller/定时任务。
测试隔离禁止工作区数据库连接，单独放行 `test-results/.coverage*` 作为工具产物。
未执行浏览器全流程、Docker 部署、外部行情抓取或真实账本验证；覆盖率不能代替这些验证。

## 先修复 P0 数据一致性问题

### P0-1 超量或无持仓卖出仍成功（2 项失败）

- 实现：`web/backend/api/transactions.py:203`。
- 用例：`scripts/tests/test_audit_regressions.py::test_sell_cannot_exceed_available_holding[0]`、`[100]`。
- 现象：持仓为 0 或 100 股时卖出 200 股，未抛出错误；现金照常增加，持仓被截断为 0。
- 修复方向：同一写事务内检查账户与可卖数量，拒绝不合法卖出；拒绝后现金、持仓和交易表保持不变。

### P0-2 持仓导入失败已经删除旧数据（1 项失败）

- 实现：`web/backend/api/upload.py:159`、`:186`。
- 用例：`test_failed_holdings_import_preserves_existing_rows`。
- 现象：数量无法解析时抛出 ValueError，但原有持仓已删除并提交。
- 修复方向：先解析和验证全部输入，再事务性替换；失败回滚，前端应收到真实导入结果。

### P0-3 资金流水操作清空快照持仓（1 项失败）

- 实现：`web/backend/api/accounts_db.py:318`、`:338`；`api/import_flow.py:1156`。
- 用例：`test_capital_flow_keeps_snapshot_only_holdings`。
- 现象：快照导入的 100 股没有对应交易历史；新增资金流水后持仓消失，现金基线也被归零重算。
- 修复方向：明确账户的期初现金、期初持仓和重放基线；历史不完整时保护快照状态。增删资金流水两条路径都需要覆盖。

### P0-4 历史补录与重放成本不同（1 项失败）

- 实现：`web/backend/api/transactions.py:179` 及创建交易的增量更新逻辑。
- 用例：`test_backdated_trade_agrees_with_chronological_replay`。
- 现象：10 月 1 日买入、10 月 8 日清仓、补录 10 月 2 日买入后，成本为 505.01；同一历史重放后变为 -482.93。
- 修复方向：历史补录在事务内按时间重放受影响状态，统一创建、删除和重建的口径；沿用现有净投入成本公式。

## 再修复 P1 校验、评分和策略问题

### P1-1 非法交易被 API 接受（3 项失败）

- 实现：`web/backend/api/transactions.py:23`、`:99`。
- 用例：`test_api_rejects_invalid_transactions` 的三个参数场景。
- 现象：未知交易类型、BUY 数量为 0、不存在的账户均返回 HTTP 200，并插入交易。零数量 BUY 还扣除最低佣金。
- 修复方向：类型枚举、账户存在性、日期及各类型数量/价格/费用约束；拒绝后不留下交易记录。补充非有限数值、负费用等边界。

### P1-2 缺少距离因子污染有效 PB/PE 总分（1 项失败）

- 实现：`scripts/scoring/scoring.py:276`；`score_universe.py` 同类判断也应检查。
- 用例：`test_missing_distance_does_not_poison_valid_valuation_scores`。
- 现象：两只股票的 PB、PE 均有效，缺距离因子的股票因 NaN 传播被排除，另一只正常进入优选池。
- 修复方向：识别 None、NaN、Inf，对有效参与因子重新归一化。PB/PE 缺失时剔除候选是当前明确规则，本轮不要求改变该规则。
- 说明：此前审查的“缺 PE 应进入池”描述已收窄，以本用例的有效 PB/PE + 缺距离因子场景为准。

### P1-3 缺失 PB 中断策略生成（1 项失败）

- 实现：`scripts/strategy/engine.py:235`。
- 用例：`test_missing_pb_does_not_abort_all_strategy_signals`。
- 现象：`from_buy_pb` 模式下首买 PB 和当前 PB 都缺失，格式化日志触发 TypeError。
- 修复方向：安全格式化缺失值，给该股票明确 HOLD/SKIP 结果，隔离单股票数据异常。

### P1-4 公告日未知数据被当作历史已知指标（1 项失败）

- 实现：`scripts/etl/build_fundamentals_and_valuation.py:483`。
- 用例：`test_unknown_announcement_date_is_not_point_in_time_data`。
- 现象：报告期 3 月 31 日、公告日 NULL 的指标，在 4 月 1 日被计算为 PB=1、PE=5。
- 修复方向：区分展示用估算与严格历史/回测数据；未知公告日不得作为严格 PIT 数据。若保留估算，需记录来源标记并在回测中排除。
- 验收口径：本用例表达严格 PIT 要求，数据用途策略需在实现中明确。

## 前端恢复交互问题

实现：`web/frontend/src/components/settings/BackupRestorePanel.vue`。  
回归文件：`web/frontend/src/components/settings/BackupRestorePanel.test.ts`。

### P2-1 暂存恢复后自动刷新，缺少持续待重启状态

- 失败用例：`keeps a visible pending-restart state without reloading after staging`。
- 现象：接口返回“数据库仅暂存，重启生效”后仍安排一秒自动刷新；刷新不会应用快照，会清掉重启提示。
- 修复方向：显示持续的待重启状态，取消自动刷新，明确重启前继续使用旧库以及新增写入会被快照替代。

### P2-2 上传失败后无限 loading 提示没有关闭

- 失败用例：`closes the indefinite loading message when upload fails`。
- 现象：网络失败进入 catch，loading 的 close 调用为 0 次；无期限“正在恢复”提示仍存在。
- 修复方向：统一在 finally 清理 loading，正确区分取消确认与请求失败。

## 待补充验证的静态风险与性能建议

以下不是本轮失败计数的一部分：

- `web/backend/api/system.py:542`：恢复 ZIP 仍完整读入内存，缺少上传及展开大小/成员数限制；需确定备份限额并补恶意压缩包回归。
- `web/backend/api/system.py:1693`：港股入库尚未接入共享市场写锁；补与 A 股 ETL/drip 并发的隔离验证。
- 用户库交易、资金流水、策略记录的查询索引应按实际查询计划补齐；避免先修改业务公式或行业锚点。
- 前端多数页面未覆盖；优先补 TradeDialog、ImportFlow、CapitalFlows 的异常与金额显示测试。ECharts/Element Plus 生产包合计约 672 KB gzip，可评估按需加载收益。

## Claude Code 复跑及验收

在项目根目录执行：

```bash
# 定位后端失败场景
.test-env/venv/bin/python -m pytest scripts/tests/test_audit_regressions.py -v

# 完整后端测试和覆盖率
.test-env/venv/bin/python -m pytest --cov=web.backend --cov=scripts.scoring \
  --cov=scripts.strategy --cov=scripts.etl --cov-report=term \
  --cov-report=xml:test-results/backend-coverage.xml \
  --junitxml=test-results/backend-tests.xml

# 前端回归、完整测试及构建
(cd web/frontend && npm test -- src/components/settings/BackupRestorePanel.test.ts)
(cd web/frontend && npm run test:coverage -- --coverage.reportOnFailure)
(cd web/frontend && npm run build)
git diff --check
```

目标为现有 160 项后端、21 项前端全部通过，构建通过，并补充修复相关边界测试。
本轮没有修改交易公式、行业锚点、定时任务时间或真实数据库；所有新增数据仅位于临时测试数据库。
