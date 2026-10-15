# 账号与业务隔离 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付账号与业务隔离，各任务独立验证并受总计划阶段门槛约束。

**Architecture:** 在现有单进程 FastAPI + SQLite 上增加明确身份、统一调用控制和可验证的适配器；React 延续现有页面。后台任务和费用状态持久化，不引入 Redis、Celery 或新的数据库。

**Tech Stack:** Python（计划基线 3.12）、FastAPI、aiosqlite、React 19、Vite 8、Vitest、pytest、Docker Compose、macOS 原生 Ollama。

**Spec:** [需求说明 v0.40](../specs/2026-10-14-local-deployment-multi-user-requirements.md)；[总计划](2026-10-15-local-deployment-multi-user-implementation.md)。

## Global Constraints

- 本文最初仅为实施计划；用户已于 2026-10-15 授权逐项实施，A01/A02 实际进度见下文及执行清单。部署、系统设置和真实外部调用不在本阶段执行范围。
- 以需求 v0.40 为准；全部已确认首版范围保留，优先级只影响顺序。不得自行缩减模型厂商或 Windows 原生验证。
- 从空数据库开始，不迁移历史业务；已有非空无归属数据库不得自动分配给管理员或自动清空。
- 唯一可信管理员；普通用户数据隔离。固定会话 30 天，改密／重置／停用撤销旧会话。
- 用户选择模型，管理员只开放服务。仅本地模式不得自动转云端；用户及全局日月预算任一未配置／为零即阻止用户云端调用。
- 调用明细默认 7 天；每日自动备份默认保留 30 天；运行日志 7 天，Trace 默认关闭、临时开启 30 分钟。
- 保持 FastAPI、React、SQLite 和现有业务结构；不做无关重构，不提交凭证、数据库或备份。

## Review Focus

1. 通过猜测资源 ID、导入导出、媒体地址和 WebSocket 绕过归属检查：必须后端拒绝，见 A04～A09。
2. 并发、跨午夜、未知用量、恢复旧库造成预算重新可用：原周期恰好结算一次，见 B01～B03、D02。
3. 切仅本地、停用或取消后后台继续发起云端步骤：每个物理请求前重查，见 A10、B05～B07。
4. 同源不同域名被误当独立备用、缺价补零或免费搜索自动付费：见 C01～C04。
5. 重启、换账号、恢复、通知超时的迟到结果污染新状态或重复发送：见 A03、B06、C07、D02。

---
## 文件与接口边界

以下路径是计划落点，不代表文件已存在。现有组件名在实施前用文件检索确认；名称不同则在同职责组件修改，不能因此重复建页面。每个任务列出的业务文件之外，必须新增所列测试并更新被改变的直接调用点；跨多个资源族的任务按资源族分小提交，不把机械调用点遗漏在范围外。新增模块职责仅限任务描述，数据库沿用现有模块并增加有版本的非破坏升级。

统一接口以[总计划的接口表](2026-10-15-local-deployment-multi-user-implementation.md#接口约定)为准。任务输入为依赖任务的已验收接口；输出为下表的功能、测试和配置说明。

### A01：运行目录与隔离测试基线

**执行状态：** 已完成（2026-10-15）；实现提交 `2f8ca22`，验收记录提交 `d4a811f`。详见 [A01 验收记录](../../../tasks/evidence/a01.md)及[执行清单](../../../tasks/todo.md)。

**依赖：** 无。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/runtime_paths.py；run.py；config.example.py；tests/conftest.py。新增测试：`tests/test_runtime_paths.py`；涉及交互时补 `frontend/src/__tests__/runtime_paths.test.jsx`。

**交付／实现：** 统一 LICAI_DATA_DIR 下 db、media、cache、backups、config；测试模式不读取真实 .env、Keychain，不启动定时任务，默认禁止外网。保留原生兼容路径的显式说明，发现旧非空库先阻止初始化。

- [x] 写确定性用例：`test_test_profile_cannot_open_real_database` 验证测试库路径不同于生产路径、旧库内容保持不变；覆盖固定时钟下的备份路径保护，外部副作用被测试夹具拦截，不使用真实凭证。A01 尚无用户模型，双用户业务隔离用例随 A02～A09 实施，不在本项宣称通过。
- [x] 实现前运行 `venv/bin/python -m pytest tests/test_runtime_paths.py -q`，确认缺少目标模块及保护行为导致失败；后续台账路径、备份软链接和评测库回退问题均记录先失败后通过的证据。
- [x] 按上述边界实现最小闭环，更新数据库、媒体、缓存、备份及辅助脚本调用点与配置说明；发现旧非空库仍按需求阻止初始化，不自动迁移或清空。
- [x] 再运行 `venv/bin/python -m pytest tests/test_runtime_paths.py -q`，19 项通过。本项无前端变更，未运行前端测试与构建；Python 3.12、Windows、Docker 及供应商实测均未标记通过。
- [x] 完成独立审查，修复台账直连和备份软链接越界两项问题；执行 `venv/bin/python -m pytest -q`，777 项通过、16 条既有弃用警告；`git diff --check` 通过，代码及验收记录已提交。

### A02：账号、初始化与本机恢复

**执行状态：** 已完成（2026-10-15）；实现提交 `c63f1fa`，详见 [A02 验收记录](../../../tasks/evidence/a02.md)及[账号使用说明](../../deployment/accounts.md)。D03 基础审计同步接入；登录/改密页面、完整会话及用户管理界面仍按 A03/A10 实施。

**依赖：** A01。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/accounts.py；api/auth_routes.py；database.py；scripts/admin_recovery.py。新增测试：`tests/test_account_bootstrap.py`；涉及交互时补 `frontend/src/__tests__/account_bootstrap.test.jsx`。

**交付／实现：** 新增 users、sessions、一次性初始化状态；密码 Argon2 哈希；本地随机初始化凭据单次消费，事务保证唯一管理员。管理员只能建普通用户，临时密码首次改密。恢复 CLI 不接收明文命令行密码参数、不清业务数据。

- [x] 新增 `test_bootstrap_race_has_one_admin`，以及固定时钟下凭据过期/轮换、两名普通用户越权、密码恢复撤销多设备旧会话和保留业务数据等用例；仅使用隔离数据库、合成凭据与伪造外部输入。
- [x] 实现前运行 `venv/bin/python -m pytest tests/test_account_bootstrap.py -q`，初始 14 项因账号/恢复模块缺失失败；凭据轮换持久化失败、账号版本不兼容和恢复误触发迁移等边界另有先失败后通过记录。
- [x] 新增 users/sessions/bootstrap_state、Argon2 密码哈希、一次性本机凭据、初始化 API、服务端普通用户创建/重置及隐藏输入的恢复 CLI；账号写入与基础审计同事务。凭据文件按代保存，恢复不运行业务迁移；不自动认领无归属业务数据。
- [x] `venv/bin/python -m pytest tests/test_account_bootstrap.py -q`：后续修复后 24 项通过。本项无前端变更，未运行前端测试/构建；Python 3.12、Windows、Docker 及真实外部服务仍未实测，不据此标记相应任务通过。
- [x] 完成独立审查并修复凭据轮换失败问题；另修复本机恢复误触发业务迁移。后续修复公共代理配置被误判为私人历史数据的问题（`548c63f`），覆盖重复启动及私人配置仍被拒绝；全量 `venv/bin/python -m pytest -q`：801 项通过、16 条既有弃用警告；差异检查通过，详情见验收记录与执行清单。

### A03：登录会话与前端账号切换

**依赖：** A02。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/auth.py；api/auth_routes.py；frontend/src/App.jsx；frontend/src/hooks/useApi.js；frontend/src/hooks/useWebSocket.js。新增测试：`tests/test_sessions.py`；涉及交互时补 `frontend/src/__tests__/sessions.test.jsx`。

**交付／实现：** 增加登录／改密页面组件；服务端不透明会话，固定30天，多设备共存；统一401处理、CSRF和Origin校验。切换清空查询缓存和私有页面，关闭旧WebSocket及重连定时器，退出仅撤当前会话。

- [ ] 写确定性用例：`test_fixed_expiry_and_logout_scope：轮询不续期，退出设备A不影响B，改密使A/B均失效；前端账号切换无旧状态`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_sessions.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A04：持仓、交易与其他资产归属

**依赖：** A03。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** database.py；api/portfolio_routes.py；api/assets_routes.py；services/position_ledger.py。新增测试：`tests/test_ownership_portfolio.py`；涉及交互时补 `frontend/src/__tests__/ownership_portfolio.test.jsx`。

**交付／实现：** 私有表及联合唯一键增加 user_id，数据库函数要求显式身份；同一股票可被不同用户独立持有。更新相关服务调用和原有用例，不增加默认管理员身份兼容绕过。

- [ ] 写确定性用例：`test_same_symbol_isolated_and_foreign_id_denied：A/B同代码持仓可并存，篡改交易ID无权读取或修改`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_ownership_portfolio.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A05：现金流、定投、券商与个人策略归属

**依赖：** A04。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** database.py；api/cashflow_routes.py；api/dca_routes.py；api/broker_routes.py；api/settings_routes.py。新增测试：`tests/test_ownership_settings.py`；涉及交互时补 `frontend/src/__tests__/ownership_settings.test.jsx`。

**交付／实现：** 逐项隔离现金流、定投、券商费率、自选分组、投资论点、风险与提醒配置；区分公共 app_config 与用户设置。修改关联前端请求，原业务规则保持。

- [ ] 写确定性用例：`test_private_settings_do_not_share_keys：相同月份／券商／分组在不同用户下独立，批量操作不能越权`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_ownership_settings.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A06：问答、媒体与派生缓存归属

**依赖：** A05。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** api/ask_routes.py；services/ask_runs.py；services/stock_agent.py；services/chart_render.py；database.py。新增测试：`tests/test_ownership_ask.py`；涉及交互时补 `frontend/src/__tests__/ownership_ask.test.jsx`。

**交付／实现：** 问答会话、消息、任务、图表媒体、工具结果都带 owner；媒体经过鉴权路由，不暴露静态私有目录。盘点 portfolio_curve/exposure、复盘、tool_gap/prompt_rule 等缓存，含个人输入或派生结果的键包含身份。该任务按资源族拆小提交。

- [ ] 写确定性用例：`test_foreign_run_media_and_cached_analysis_denied：猜中run/session/media标识仍拒绝，两个用户相同问题不共享私有回答`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_ownership_ask.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A07：私人来源与用户级凭证

**依赖：** A06。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/okx_client.py；services/zsxq_client.py；providers/registry.py；api/settings_routes.py。新增测试：`tests/test_private_connections.py`；涉及交互时补 `frontend/src/__tests__/private_connections.test.jsx`。

**交付／实现：** 把全局单例凭证改为按用户创建／获取客户端；插件代码仍由宿主管理。凭证写入后只回显掩码，摘要、连接状态、授权、错误和缓存按用户隔离；公共来源仍管理员配置。

- [ ] 写确定性用例：`test_two_credentials_do_not_reuse_client_or_cache：A/B凭证及订阅互不串用，日志无原文`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_private_connections.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A08：个人导入导出与业务权限清单

**依赖：** A07。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** api/export_routes.py；api/news_routes.py；api/briefing_routes.py；api/sector_routes.py。新增测试：`tests/test_ownership_routes.py`；涉及交互时补 `frontend/src/__tests__/ownership_routes.test.jsx`。

**交付／实现：** 导出仅本人；导入仅允许业务表、服务端指定owner，拒绝认证／全局配置／预算账本等表。扫描全部HTTP路由，明确公开健康／初始化、已登录业务、管理员三类；报告、快照和分析入口也检查身份。

- [ ] 写确定性用例：`test_import_cannot_set_owner_or_replace_auth：伪造user_id和sessions表不能影响他人；逐路由权限矩阵通过`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_ownership_routes.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A09：WebSocket 与行情推送隔离

**依赖：** A08。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** api/ws.py；services/portfolio_snapshot.py；frontend/src/hooks/useWebSocket.js。新增测试：`tests/test_ownership_websocket.py`；涉及交互时补 `frontend/src/__tests__/ownership_websocket.test.jsx`。

**交付／实现：** 连接绑定会话与用户，检查Origin；公共报价可聚合抓取，但持仓、触发规则和个人事件只发owner。失效连接及时关闭；断线重连重做认证。

- [ ] 写确定性用例：`test_shared_quote_does_not_broadcast_private_holdings：公共报价复用但私有字段不进入B连接，过期或停用连接断开`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_ownership_websocket.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### A10：账号停用、后台任务与基础管理入口

**依赖：** A09。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/task_context.py；services/scheduler.py；api/ws.py；run.py；frontend/src/components/AdminUsers.jsx。新增测试：`tests/test_account_lifecycle.py`；涉及交互时补 `frontend/src/__tests__/account_lifecycle.test.jsx`。

**交付／实现：** 新增账号管理和清单；调度保留原有非AI功能，个人任务携带身份，自动AI默认关闭。停用撤销会话、取消排队并阻止新通知／调用，启用不补跑；禁止停用唯一管理员。清理跨用户全局_done_date标记。

- [ ] 写确定性用例：`test_disable_cancels_future_steps_without_deleting_data：停用后新步骤为0，保留数据，启用不回放；后台退出语义符合需求`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_account_lifecycle.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

## 阶段复核

- [ ] 逐项对照总计划的 AC 追踪表，失败项阻止开放对应功能。
- [ ] 检查没有默认身份、费用为零假设、真实凭证测试或对生产数据的隐式恢复。
- [ ] 将真实联调、未验证环境和可复现失败分别记录；未满足依赖时不把任务标为完成。
