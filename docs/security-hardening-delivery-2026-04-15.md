# 安全修复交付说明

本轮 `/cso` 安全加固已完成，相关修复已在本地代码和重建后的运行环境中验证通过。

## 提交列表

1. `ee24bbe fix(security): protect admin routes and secrets`
2. `8c5eac0 fix(security): require admin token for api writes`
3. `b585a16 fix(security): harden build supply chain`
4. `a9b1e65 fix(security): harden file upload boundaries`
5. `4de1501 fix(security): validate task trigger inputs`
6. `c4dda4a fix(security): reduce token and error exposure`

## 修复范围

### 1. 管理接口未鉴权

- `system/settings` 等高权限接口现在要求 `ADMIN_TOKEN`
- 所有 `/api/**` 的 `POST/PUT/PATCH/DELETE` 默认要求管理员令牌

### 2. 敏感配置泄露

- `tushare_token` 不再明文返回前端
- 前端仅能拿到 `masked` 和 `has_value`

### 3. 备份恢复高风险入口

- `/system/restore` 去掉了 `extractall`
- 改为白名单 + 路径规范化 + 逐文件恢复
- 阻断 zip 路径穿越

### 4. 前端管理员令牌暴露窗口过长

- 管理员令牌从 `localStorage` 改为 `sessionStorage`
- 降低 XSS 命中后的长期暴露风险
- 保留旧令牌迁移逻辑，避免当前会话直接失效

### 5. 文件上传与导入边界不足

- 上传类接口增加文件大小限制
- `account_id` 等路径组件增加合法字符校验
- 阻断目录穿越和大文件内存打满
- 截图导入增加图片类型和大小限制

### 6. 后台任务触发参数过宽

- `/system/sync-hk-prices` 现在校验 `start` 和 `codes`
- 限制港股代码格式和最大数量
- 降低被滥用成重活放大器的风险

### 7. 构建供应链可漂移

- 后端 Docker 构建不再从 GitHub `main.zip` 拉取 `pytdx3`
- 改为安装仓库内 `pytdx3/` 子模块
- 前端 Docker 构建从 `npm install` 改为 `npm ci`
- 前端 lockfile 已通过 `npm audit fix`
- 前端依赖漏洞从 `9` 个降到 `0`

### 8. 5xx 错误细节泄露

- 全局异常处理已做脱敏
- 5xx 返回体不再暴露数据库、路径、脚本 stderr 等内部细节

## 验证结果

### 代码级验证

- 后端安全相关测试：`15 passed`
- 前端构建：通过
- Docker 镜像重建：通过
- 前端镜像安装阶段确认：`found 0 vulnerabilities`

### 运行时验证

- 容器内 `ADMIN_TOKEN` 已正确注入
- 匿名访问管理接口返回 `401`
- 带令牌访问管理接口返回 `200`
- `tushare_token` 返回为遮罩形式
- 5xx 返回体已脱敏，不再泄露内部异常文本

## 当前结论

这批安全修复已经从“代码已改”推进到“运行环境已生效”。

当前可以进入合并、发 PR 或上线流程。

## 后续建议

以下项不阻塞本轮交付，建议后续单独排期：

- 生产日志进一步脱敏，减少异常文本在日志中的敏感信息暴露
- 对部分高成本后台任务增加更明确的频率控制或审计记录
- 如未来需要更强安全边界，可把管理员令牌机制升级为独立登录态或更细粒度权限模型
