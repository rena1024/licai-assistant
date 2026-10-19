# 受控多模型与用量 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付受控多模型与用量，各任务独立验证并受总计划阶段门槛约束。

**Architecture:** 在现有单进程 FastAPI + SQLite 上增加明确身份、统一调用控制和可验证的适配器；React 延续现有页面。后台任务和费用状态持久化，不引入 Redis、Celery 或新的数据库。

**Tech Stack:** Python（计划基线 3.12）、FastAPI、aiosqlite、React 19、Vite 8、Vitest、pytest、Docker Compose、macOS 原生 Ollama。

**Spec:** [需求说明 v0.40](../specs/2026-10-14-local-deployment-multi-user-requirements.md)；[总计划](2026-10-15-local-deployment-multi-user-implementation.md)。

执行状态（2026-10-19 当前复核）：原计划核心实现与离线验证、统一 review 修复完成；用户反馈的模型配置交互已补齐。**B 尚未全部完成**：B02 新增 DeepSeek 分时计价待实现，完整厂商／场景实测未通过。本机 `local_ollama` v1 文本已有成功记录，不能外推其他能力。当前代码基线 `4018548`，最新逐项证据与回归结果见 `tasks/evidence/b-execution.md` 的「B 阶段当前复核」。

## Global Constraints

- 2026-10-19 用户授权 B01～B09 连续实施，全部结束后统一 review。实现及离线进度见 `tasks/evidence/b-execution.md`；不部署、不改系统设置、不执行真实付费请求或外发通知。
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

### B01：模型目录、配置与统一协议

**依赖：** A10。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/llm_types.py；services/model_catalog.py；api/model_routes.py；database.py。新增测试：`tests/test_model_catalog.py`；涉及交互时补 `frontend/src/__tests__/model_catalog.test.jsx`。

**交付／实现：** 定义统一请求／响应／工具／usage接口，服务端校验端点和能力；配置版本化。管理员开服务、普通用户每场景选主模型及有序备用，可保存无候选仅本地；禁用保留失效选择不强行代换。

- [x] 写确定性用例：`test_user_owns_scene_choice：管理员不能替用户选模型；不合能力候选被跳过，配置失败不覆盖有效版本`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_model_catalog.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B02：预算预留、尝试账本与结算

**依赖：** B01。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/budget.py；services/usage.py；database.py。新增测试：`tests/test_budget_ledger.py`；涉及交互时补 `frontend/src/__tests__/budget_ledger.test.jsx`。

**交付／实现：** 金额Decimal／整数微元存储；用单次SQLite事务检查全局+用户日月4项并预留，每个物理attempt独立ID；诊断仅占全局。价格／汇率／周期快照不可回算，未知usage保留预留并暂停该服务新云端请求，人工核对需理由与审计。

**当前状态：** 下列原计划固定单价实现及验证已完成；新增分时计价未实现，因此 B02 整体重新打开。历史通过记录保留，不表示新增要求通过。

- [ ] B02-F01：支持 DeepSeek 高峰／空闲两组价格及北京时间时段／法定节假日；明确跨时段估算口径，按保守上界预留，保留所用时段及单价快照，覆盖边界／重试／历史不回算。与 B08 的厂商字段映射联动，不把固定高峰价估算当作分时支持。

- [x] 写确定性用例：`test_atomic_reservations_never_exceed_limit：并发只成功可承受的预留；另测跨午夜迟到结算、重复回调、缺价/零预算、未知用量不释放`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_budget_ledger.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B03：用量页面、阈值提醒与安全清理

**依赖：** B02。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** api/usage_routes.py；services/usage.py；frontend/src/components/UsagePanel.jsx；services/retention.py。新增测试：`tests/test_usage_retention.py`；涉及交互时补 `frontend/src/__tests__/usage_retention.test.jsx`。

**交付／实现：** 先交付用户本人用量及管理员聚合、估算／未知标签、80%提醒和100%停止；聚合／提醒去重独立于7天明细。管理员不能借账单读提示词和回答；未结算最小财务依据不随明细清理。

- [x] 写确定性用例：`test_detail_purge_keeps_month_totals_and_pending_reservations：删除旧明细不改余额和提醒去重，用户不能查别人的用量`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_usage_retention.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B04：首条受控模型链：Ollama 与 Anthropic

**依赖：** B03。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/llm_runtime.py；services/llm_adapters/ollama.py；services/llm_adapters/anthropic.py；services/llm_client.py。新增测试：`tests/test_llm_attempts.py`；涉及交互时补 `frontend/src/__tests__/llm_attempts.test.jsx`。

**交付／实现：** 统一入口包住每次真实HTTP发送、重试、工具下一轮；消除旧客户端隐藏重试和默认Keychain/CLI绕过。先打通本地与现有Anthropic场景，不在预算完成前开放新云端路径。本地端点验证排除Ollama远程云模型。

- [x] 写确定性用例：`test_each_retry_is_authorized_and_accounted：两次HTTP等于两个attempt；缺预算零出网，仅本地不接受远程cloud模型`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_llm_attempts.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B05：全局本地队列与公平调度

**依赖：** B04。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/local_queue.py；services/task_context.py；services/scheduler.py。新增测试：`tests/test_local_queue.py`；涉及交互时补 `frontend/src/__tests__/local_queue.test.jsx`。

**交付／实现：** 默认全局本地并发1，交互优先、用户轮转、后台老化防饥饿；数据库记录状态，进程启动把遗留运行任务中断。无候选／排队超时不逃往云端；不抢占正在推理的任务。

- [x] 写确定性用例：`test_global_concurrency_and_background_fairness：多用户推理最大并发1，持续交互下后台最终获得机会，取消排队即退出`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_local_queue.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B06：问答任务、备用版本与取消

**依赖：** B05。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/ask_runs.py；services/llm_runtime.py；api/ask_routes.py；frontend/src/components/AskPanel.jsx。新增测试：`tests/test_ask_lifecycle.py`；涉及交互时补 `frontend/src/__tests__/ask_lifecycle.test.jsx`。

**交付／实现：** 先核对实际问答组件名称再落点；新提交幂等复用活动任务。失败半段保留，备用新建完整回答版本；每步重查模式、用户、预算和取消标记；页面关闭或普通退出不取消已提交任务。迟到结果不可覆盖终态但仍结算。

- [x] 写确定性用例：`test_cancel_and_mode_change_block_next_attempt：取消后模型/工具/备用调用为0；切仅本地阻止旧任务后续云端；刷新不重跑`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_ask_lifecycle.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B07：迁移全部 AI 场景与后台作业

**依赖：** B06。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/stock_agent.py；services/morning_briefing.py；services/fundamental_score.py；services/coiled_scanner.py；services/multi_lens.py。新增测试：`tests/test_ai_entrypoints.py`；涉及交互时补 `frontend/src/__tests__/ai_entrypoints.test.jsx`。

**交付／实现：** 逐场景移除硬编码Claude与厂商联网工具，HTTP路由内AI也迁移；先用清单锁定所有调用点，按场景分提交。私有分析带owner，定时任务先用户主动启用；错过早盘不伪装补报，手动生成注明实际时点。

- [x] 写确定性用例：`test_no_cloud_path_bypasses_runtime：替换统一发送器后全部场景可截获；扫描遗留直连；退出任务继续、停用不新增步骤`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_ai_entrypoints.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B08：OpenAI、DeepSeek、通义千问适配

**依赖：** B07。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/llm_adapters/openai.py；services/llm_adapters/deepseek.py；services/llm_adapters/qwen.py；tests/fixtures/llm/。新增测试：`tests/test_llm_cloud_adapters.py`；涉及交互时补 `frontend/src/__tests__/llm_cloud_adapters.test.jsx`。

**交付／实现：** OpenAI首选Responses原生映射；DeepSeek/Qwen按官方当前协议分别映射，不能仅换base_url假设兼容。共享代码只限已证实一致部分；流、工具、错误、缓存/推理token均做计价映射。

**当前状态：** 协议与固定单价字段映射离线通过；DeepSeek 表单已改为缓存命中输入、未命中输入、输出三项。分时价格适配仍随 B02-F01 待实现，真实厂商矩阵随 B-V01 待验收。下述 fixture 为按公开协议构造的合成数据，不是真实请求抓包。

- [x] 写确定性用例：`test_protocol_fixtures_normalize_tools_usage_and_errors：各厂商真实结构fixture映射一致，不重复计算cache/reasoning，未知字段不会变成0费用`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_llm_cloud_adapters.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### B09：Gemini、自定义兼容与能力矩阵

**依赖：** B08。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/llm_adapters/gemini.py；services/llm_adapters/openai_compatible.py；services/model_catalog.py；frontend/src/components/ModelSettings.jsx。新增测试：`tests/test_llm_capabilities.py`；涉及交互时补 `frontend/src/__tests__/llm_capabilities.test.jsx`。

**交付／实现：** Gemini原生generateContent保留工具所需签名和结构；自定义服务显式选择协议。模型文本／结构化／工具／图像能力逐项验证；用户可按场景配置，展示实际版本、失败原因与云端／本地状态。

- [x] 写确定性用例：`test_capability_failure_uses_only_selected_eligible_backup：图像或工具不支持时仅尝试用户候选；无本地可保存偏好但AI明确不可用`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_llm_capabilities.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

## 阶段复核

当前结论：原计划实现与离线验证完成，新增分时需求及完整实测未完成，不能标整个 B 完成。以下前三项是首轮复核记录，当前剩余项另列。

- [x] 逐项对照总计划的 AC 追踪表，失败项阻止开放对应功能。真实联调单独保留。
- [x] 检查没有默认身份、费用为零假设、真实凭证测试或对生产数据的隐式恢复。
- [x] 将真实联调、未验证环境和可复现失败分别记录；未满足依赖时不把任务标为完成。
- [ ] B02-F01 分时计价完成并通过回归。
- [ ] B-V01 完整厂商／场景支持矩阵实际验收；当前仅 Ollama 文本有真实成功记录。
- [ ] B-V02 总计划 B 完整门槛通过；平台与交付依赖见 D04／D06～D08。
