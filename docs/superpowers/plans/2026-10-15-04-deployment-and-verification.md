# 部署运维与交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付部署运维与交付，各任务独立验证并受总计划阶段门槛约束。

**Architecture:** 在现有单进程 FastAPI + SQLite 上增加明确身份、统一调用控制和可验证的适配器；React 延续现有页面。后台任务和费用状态持久化，不引入 Redis、Celery 或新的数据库。

**Tech Stack:** Python（计划基线 3.12）、FastAPI、aiosqlite、React 19、Vite 8、Vitest、pytest、Docker Compose、macOS 原生 Ollama。

**Spec:** [需求说明 v0.40](../specs/2026-10-14-local-deployment-multi-user-requirements.md)；[总计划](2026-10-15-local-deployment-multi-user-implementation.md)。

## Global Constraints

- 本文最初仅为实施计划；用户已授权逐项实施。D03 基础账号审计已随 A02 接入，其他部署运维任务按各节状态执行；本阶段不部署、不修改系统设置、不执行真实外部调用。
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

### D01：一致性备份与最小运行健康

**执行状态：** 2026-10-16实现及离线验收完成，见 [D集成证据](../../../tasks/evidence/d-first-batch.md)。管理员消息中心失败通知待C05，目前管理员维护页和白名单运行事件呈现。

**依赖：** A10；在批次A验收前完成。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/backup.py；api/admin_routes.py；api/ws.py；services/retention.py。新增测试：`tests/test_backup_consistency.py`；涉及交互时补 `frontend/src/__tests__/backup_consistency.test.jsx`。

**交付／实现：** 替换live DB shutil.copy2为SQLite备份API；连同必要配置/媒体创建版本化清单、摘要及一致恢复点；媒体使用不可变文件名并按数据库快照引用复制，备份期间阻止对应文件清理，失败不发布半成品。每日一次、启动错过补一次，自动30天；基本健康检查不真实发通知/模型。备份访问仅admin，不提供内容浏览。

- [x] 写确定性用例：`test_backup_during_writes_is_restorable：并发写时快照完整、含必要媒体；清理不删唯一有效点；失败只通知管理员`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_backup_consistency.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D02：受保护恢复与费用隔离

**执行状态：** 2026-10-16基础恢复及故障注入完成；预算联动待B02、通知状态机联动待C05～C07，整体未标完成。见 [D集成证据](../../../tasks/evidence/d-first-batch.md)。

**依赖：** D01；B02接口完成后补预算集成。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/restore.py；services/backup.py；services/auth.py；services/task_context.py。新增测试：`tests/test_restore_safety.py`；涉及交互时补 `frontend/src/__tests__/restore_safety.test.jsx`。

**交付／实现：** 先校验兼容性/摘要/完整性再保护备份，维护态停写并使旧worker代际失效，原子替换后撤销全会话、运行任务转中断；恢复出的待投递通知不自动重发，先进入待管理员核对状态。云端保持恢复隔离直到人工核对费用；恢复前仅本地偏好按稳定用户UUID取与备份的更严格并集，用户不在备份则不复活其任务。

- [x] 写确定性用例：`test_restore_failure_keeps_current_db_and_cloud_hold_survives_restart：损坏/保护备份失败均不覆盖；旧结果不落新库，旧余额不开放云端`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_restore_safety.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D03：脱敏审计、日志和 Trace

**执行状态：** 本批管理／配置／备份恢复审计、白名单结构日志、独立保留清理及Trace持久到期已接入，管理员维护页只展示元信息。2026-10-16隐私4项、集成及完整回归通过，见 [D集成证据](../../../tasks/evidence/d-first-batch.md)。

**尚待实施：** 历史print／第三方输出全面迁移，B02后的费用核对审计及后续模块动作覆盖；D03整体保持未完成，不把当前结构日志模块当作所有旧输出已脱敏。

**依赖：** A02起接入；A10完整。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/audit.py；services/logging_config.py；services/retention.py；api/admin_routes.py。新增测试：`tests/test_audit_privacy.py`；涉及交互时补 `frontend/src/__tests__/audit_privacy.test.jsx`。

**交付／实现：** 账号/重置/配置/预算核对/恢复等管理操作从首次实现即写脱敏审计，界面后补。日志7天，Trace显式到期时间持久化，30分钟自动停，重启不延长；白名单结构字段，禁止提示词、答案和凭证。

- [ ] 写确定性用例：`test_trace_expiry_and_redaction：时钟推进/重启均到期，异常对象中密钥和完整业务正文不进入日志/审计`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_audit_privacy.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D04：Mac Docker 打包与升级验证

**执行状态：** 2026-10-17批次A最小打包及真实独立卷重建／HTTP恢复完成，原生与Linux/Python3.12回归各872项。真实构建修复中文字体、同名JS引擎覆盖及root缓存归属。完整升级、宿主Ollama及后续平台实测未完成，D04整体不勾选，见 [D04证据](../../../tasks/evidence/d04.md)。

**依赖：** A01后可预研；A与D01通过才供日常使用。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** Dockerfile；compose.yaml；.dockerignore；docs/deployment/macos-docker.md。新增测试：`tests/test_container_contract.py`；涉及交互时补 `frontend/src/__tests__/container_contract.test.jsx`。

**交付／实现：** 多阶段构建，Node22>=22.12/Python3.12作为候选并锁定实测补丁版本，非root单进程，无Docker socket。仅挂载持久数据，排除.env/DB/备份。容器连接host.docker.internal原生Ollama，restart策略；升级前备份，旧镜像不盲写新版schema。

- [ ] 写确定性用例：`test_runtime_paths_and_health_contract：镜像无密钥，重建数据在、空库不造演示数据；另用Docker实际重建验证，不能以单元测试替代`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_container_contract.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D05：预定义工程诊断与隔离回归入口

**依赖：** B09；C07；D03。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/diagnostics.py；api/admin_routes.py；frontend/src/components/Diagnostics.jsx；compose.test.yaml。新增测试：`tests/test_diagnostic_boundary.py`；涉及交互时补 `frontend/src/__tests__/diagnostic_boundary.test.jsx`。

**交付／实现：** 基础冒烟先可用；完整页面后做。回归由固定隔离测试worker执行预定义清单，通过受限本地请求目录/协议提交，不挂Docker socket或开放shell；测试容器按需手动启动。缺环境/资源明确拒绝。外部测试单独点击、标注费用/发送副作用，系统模型诊断只计全局。

- [ ] 写确定性用例：`test_regression_worker_cannot_read_production_or_send：测试无生产挂载/凭证、默认禁止外网；请求不能注入命令，重复任务复用或拒绝`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_diagnostic_boundary.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D06：Mac 原生、登录后启动与 Ollama 恢复

**依赖：** D04；A与D01通过。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** docs/deployment/macos-native.md；docs/deployment/macos-docker.md；scripts/macos/。新增测试：`tests/test_startup_contract.py`；涉及交互时补 `frontend/src/__tests__/startup_contract.test.jsx`。

**交付／实现：** 分别说明原生和Docker启动；Docker Desktop登录项、应用重启策略、Ollama原生启动与晚就绪恢复。不自动修改休眠/自动登录/防火墙；提供停用和卸载自启动步骤，手机局域网验证。

- [ ] 写确定性用例：`test_ollama_late_readiness_does_not_crash_app：未就绪只标状态、恢复可重连；真实注销登录另记证据`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_startup_contract.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D07：Windows 原生兼容与完整指南

**依赖：** A01起持续检查；B09/C07完成全验收。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/llm_client.py；services/runtime_paths.py；scripts/windows/；docs/deployment/windows-native.md。新增测试：`tests/test_windows_compatibility.py`；涉及交互时补 `frontend/src/__tests__/windows_compatibility.test.jsx`。

**交付／实现：** 替换os.uname等无保护Mac专用逻辑；PowerShell独立步骤，虚拟环境解释器替代强制放宽策略；任务计划程序登录后启动/禁用，原生Ollama、路径/中文编码/备份恢复实测。无Windows环境不得标通过。

- [ ] 写确定性用例：`test_platform_paths_without_uname：模拟无uname不崩溃，空格/中文路径有效；须Windows11真机或VM再跑指南与回归`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_windows_compatibility.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### D08：全量回归、手机与发布记录

**依赖：** 所有前序任务。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** tests/acceptance/；docs/verification/2026-10-15-release-matrix.md；README.md；config.example.py。新增测试：`tests/test_release_acceptance.py`；涉及交互时补 `frontend/src/__tests__/release_acceptance.test.jsx`。

**交付／实现：** 两用户端到端、47项需求逐条证据、故障注入、升级/恢复演练；iPhone Safari和Android Chrome核心流程。发布记录真实版本/硬件/数据量/性能，不写未验证支持；README平台入口和安全升级步骤完整。

- [ ] 写确定性用例：`test_two_user_full_journey：初始化、隔离、模型偏好、预算、本地、搜索、消息、恢复完整链路；离线测试与真实联调结果分栏`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [ ] 实现前运行 `python -m pytest tests/test_release_acceptance.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [ ] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [ ] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [ ] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

## 阶段复核

- [ ] 逐项对照总计划的 AC 追踪表，失败项阻止开放对应功能。
- [ ] 检查没有默认身份、费用为零假设、真实凭证测试或对生产数据的隐式恢复。
- [ ] 将真实联调、未验证环境和可复现失败分别记录；未满足依赖时不把任务标为完成。
