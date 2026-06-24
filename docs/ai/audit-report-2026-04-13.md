# MyInvestmentSystem 全项目审核报告

**日期**：2026-04-13  
**审核范围**：全项目静态审核，重点覆盖后端 API、数据库、交易资金、打分引擎、ETL、定时任务、前端服务与视图联动  
**审核基准**：项目级 `AGENTS.md` + 工作区级 `AGENTS.md` + `docs/ai/project-facts.md`  
**状态**：发现 3 个 P1、1 个 P2 问题，建议交回 Claude Code 修复

---

## 审核结论

| 级别 | 数量 |
|------|------|
| P0 | 0 |
| P1 | 3 |
| P2 | 1 |
| P3 | 0 |

本次审核里，交易资金主链路的现有回归测试通过，前端构建通过；但打分链路存在配置与实现脱节，且归档链路有重复写入风险，会直接影响候选池结果和历史收益统计。

---

## 发现的问题

### [P1] `scripts/scoring/scoring.py`

**问题**：实际被后端调用的 `run_scoring()` 没有执行 `config/scoring.yaml` 中的 `negatives` 和 `major_events` 规则。当前实现只计算正向分数，并在 `scripts/scoring/scoring.py:256-270` 做了 `ST` 名称过滤；而 `config/scoring.yaml:117-145` 明确配置了三负和一重大规则。`web/backend/api/stock_pool.py:225-231` 与 `web/backend/scheduler.py:672-681` 都直接调用了这个实现，因此 Web 端“优选池”与定时评分实际上绕过了风险剔除。

**为什么重要**：这不是展示层问题，而是候选池生成逻辑错误。负债率、商誉/无形资产、股权质押、重大事件本应直接拦截候选标的，但现在只要正向分数足够高就会入池，属于打分引擎业务结果偏差。

**建议修复**：把 `score_universe.py` 里已经实现的负面条件、重大事件和硬性排除逻辑统一收敛到 `scripts/scoring/scoring.py`，或者反过来只保留一个单一评分实现，避免 API/定时任务与 CLI 走不同规则。

---

### [P1] `scripts/scoring/scoring.py`

**问题**：`score_anchor_tiers()` 的实现与 `scoring.yaml` 配置口径不一致。当前代码在 `scripts/scoring/scoring.py:74-81` 使用相对比例 `ratio = (v - a) / a` 解释 `delta`，但配置里 `config/scoring.yaml:16`、`config/scoring.yaml:51` 指定的是 `tiers_mode: additive`；同仓库的 `scripts/scoring/score_candidates.py:28-45` 也按“锚点 + delta”实现。与此同时，`config/scoring.yaml:48` 配置了 `ignore_if_le_zero: true`，但 `scripts/scoring/scoring.py:189-215` 完全没有读取这个开关，导致 `PE<=0` 会被当成“优于锚点”直接打满分。实际复现中，`score_anchor_tiers(-1, 8, tiers)` 返回 `100.0`，`score_anchor_tiers(20, 8, tiers)` 也仍返回 `90.0`。

**为什么重要**：这会直接扭曲 PB/PE 因子方向和分桶边界，属于项目规范里明确列出的高风险项。特别是负 PE 或零 PE 本应按异常值剔除，现在反而给最高分，会把明显不该入池的股票推到前面。

**建议修复**：统一 `tiers_mode` 语义，只保留一种锚点分桶实现；对 `pe_ttm` 明确执行 `ignore_if_le_zero`；为 `PB<=0`、`PE<=0`、缺失值分别补充单元测试，防止评分规则再次漂移。

---

### [P1] `web/backend/api/transactions.py` / `web/backend/api/transactions_archive.py` / `web/frontend/src/views/HoldingsAnalysis.vue`

**问题**：清仓归档链路不是幂等的，重复归档会产生多条历史记录。`web/backend/api/transactions.py:408-415` 在持仓数量归零时会自动调用 `_recalculate_archived_holdings()` 写入或更新 `archived_holdings`；但 `web/backend/api/transactions_archive.py:105-182` 的手动归档接口没有先查重，而是再次 `INSERT INTO archived_holdings`。前端在 `web/frontend/src/views/HoldingsAnalysis.vue:519-536` 又会在卖出清仓后主动弹窗提示“是否归档”，等于把用户导向第二次写归档。更糟的是，`web/backend/database.py:137-154` 的 `archived_holdings` 表没有 `(account_id, ts_code)` 唯一约束，因此重复写入不会失败，只会留下重复归档数据。

**为什么重要**：归档页会出现重复股票，累计买入/卖出/分红/收益统计被重复计算，直接污染历史业绩数据。这属于交易与归档链路的数据一致性问题。

**建议修复**：归档逻辑必须改成单一路径并保持幂等。可选方案是：
1. 清仓时只走 `_recalculate_archived_holdings()`，前端不再提示二次归档。
2. 或保留手动归档入口，但接口先按 `(account_id, ts_code)` 查重并执行 `UPSERT`。
3. 同步给 `archived_holdings` 增加唯一约束或迁移为唯一索引，防止脏数据继续累积。

---

### [P2] `scripts/scoring/scoring.py`

**问题**：`run_scoring()` 的默认路径拼接错误，CLI 直接调用会找不到配置文件。`scripts/scoring/scoring.py:152-160` 把 `project_root` 设成了当前脚本目录 `scripts/scoring/`，随后默认去读取 `scripts/scoring/config/scoring.yaml`、`scripts/scoring/stock_industry_base.csv` 等路径。实际仓库文件位于项目根目录下的 `config/scoring.yaml`、`stock_industry_base.csv`、`market_enriched.csv`。本地最小复现 `python3 -c "from scripts.scoring import scoring; scoring.run_scoring(write_csv=False)"` 会抛 `FileNotFoundError: .../scripts/scoring/config/scoring.yaml`。

**为什么重要**：虽然 API 和 scheduler 目前都显式传了路径，但这个默认行为会让脚本模式失效，也增加未来调用方误用的概率。评分脚本本身是项目关键工具，默认参数不应处于坏状态。

**建议修复**：把默认基准目录改成项目根目录，或者像 `score_universe.py` 一样强制要求显式传入配置路径；同时补一条最小 CLI 冒烟测试，确保无参或标准示例命令可运行。

---

## 验证记录

**已完成验证**

- `python3 -m pytest -q scripts/tests/test_transactions.py scripts/tests/test_delete_holding_cash_rollback.py scripts/tests/test_rebuild_holdings_preserves_buy_pb.py`
  - 结果：`16 passed in 0.38s`
- `npm run build`（`web/frontend`）
  - 结果：通过，`vue-tsc -b && vite build` 成功
- 打分函数最小复现
  - `score_anchor_tiers(-1, 8, tiers) -> 100.0`
  - `score_anchor_tiers(20, 8, tiers) -> 90.0`
- CLI 最小复现
  - `run_scoring(write_csv=False)` 抛 `FileNotFoundError`，默认路径错误

**未完全完成的验证**

- `python3 -m compileall web/backend scripts/scoring scripts/etl`
  - 由于沙箱禁止写入系统 `pycache` 缓存目录，命令触发 `PermissionError`，本次未据此下结论

---

## 总体建议

优先级建议如下：

1. 先修复 `scripts/scoring/scoring.py` 与 `config/scoring.yaml` 的口径漂移，再重新生成一次 stock pool。
2. 再修复清仓归档的幂等性和唯一约束，必要时补一段去重迁移脚本清理历史重复归档。
3. 最后补评分与归档相关单元测试，确保后续 Claude Code 修改不会再次破坏这两条高风险链路。

