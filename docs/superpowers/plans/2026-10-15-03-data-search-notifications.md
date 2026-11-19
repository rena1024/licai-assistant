# 数据搜索与通知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**执行状态（2026-10-19）：** 用户已授权 C01～C07；实现、离线回归与一次统一审查修复完成，后端893项、前端83项及构建／隔离浏览器通过。真实来源、搜索账户免费条件与通知接收人仍保留待办，不代表完整阶段交付；见 [执行证据](../../../tasks/evidence/c-execution.md)。

**Goal:** 交付数据搜索与通知，各任务独立验证并受总计划阶段门槛约束。

**Architecture:** 在现有单进程 FastAPI + SQLite 上增加明确身份、统一调用控制和可验证的适配器；React 延续现有页面。后台任务和费用状态持久化，不引入 Redis、Celery 或新的数据库。

**Tech Stack:** Python（计划基线 3.12）、FastAPI、aiosqlite、React 19、Vite 8、Vitest、pytest、Docker Compose、macOS 原生 Ollama。

**Spec:** [需求说明 v0.40](../specs/2026-10-14-local-deployment-multi-user-requirements.md)；[总计划](2026-10-15-local-deployment-multi-user-implementation.md)。

## Global Constraints

- 本文是实施计划；用户已授权实施 C01～C07。部署、系统设置、真实付费调用及真实通知外发仍未执行。
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

### C01：行情合同与独立备用

**依赖：** A09；B01接口稳定。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/quote_sources.py；services/market_data.py；api/market_routes.py；api/settings_routes.py。新增测试：`tests/test_quote_failover.py`；涉及交互时补 `frontend/src/__tests__/quote_failover.test.jsx`。

**交付／实现：** 先盘点实际行情入口及符号映射；A股/港股/美股实时报价以现有新浪+独立腾讯适配为首轮候选，按市场实测决定开放覆盖。K线沿用东财/腾讯等分能力配置，复权不混用；同源域名不算独立供应商。

- [x] 写确定性用例：`test_primary_failure_uses_independent_provider：新浪故障触发腾讯fixture，不走新浪别名；无可用源返回缺价状态并保留旧值`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_quote_failover.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C02：时效、估值覆盖与资讯来源

**依赖：** C01。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/quote_sources.py；services/news.py；services/portfolio_snapshot.py；api/portfolio_routes.py；frontend/src/components/DataQuality.jsx。新增测试：`tests/test_data_quality.py`；涉及交互时补 `frontend/src/__tests__/data_quality.test.jsx`。

**交付／实现：** 统一 source_at/fetched_at、币种单位、交易状态、stale_reason和覆盖率；收益与市值缺价不填0。资讯多源聚合，注明原始来源/时间，非线性备用。财报报告期单列；AI输入带时效限制与可追溯引用。

- [x] 写确定性用例：`test_closed_market_is_not_outage_and_missing_price_is_not_zero：交易日历含跨时区与夏令时，缓存旧价明确标注，AI不得称最新`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_data_quality.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C03：独立免费搜索适配与额度

**依赖：** C02；B02。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/search.py；services/search_adapters/tavily.py；services/search_adapters/exa.py；api/search_routes.py。新增测试：`tests/test_search_quota.py`；涉及交互时补 `frontend/src/__tests__/search_quota.test.jsx`。

**交付／实现：** 首选Tavily、备用Exa，实际账号需证实免费、无自动付费并允许所需存储；不满足则适配器保持禁用。搜索请求有独立credits预留、全局额度与个人公平限额；未知剩余额度暂停不猜免费。模型预算不参与搜索配额。

- [x] 写确定性用例：`test_free_quota_unknown_or_exhausted_never_pays：重试也扣预留，免费备用耗尽后停止；本地和云端用相同搜索接口`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_search_quota.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C04：问答检索接入与数据中心

**依赖：** C03；B07。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/stock_agent.py；api/settings_routes.py；frontend/src/components/DataCenter.jsx；frontend/src/components/ModelSettings.jsx。新增测试：`tests/test_search_privacy.py`；涉及交互时补 `frontend/src/__tests__/search_privacy.test.jsx`。

**交付／实现：** 新增管理员数据中心：行情、K线、资讯、搜索各自来源/顺序/状态/最近成功时间；用户独立开关搜索和管理私人连接。搜索入口沿用问答，不建新门户；搜索词仅必要关键词，过滤账户标识、持仓数额及成本。

- [x] 写确定性用例：`test_search_query_excludes_private_account_context：同一问题在不同模型下数据源不变，搜索关闭后无外部检索，普通用户看不到公共密钥`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_search_privacy.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C05：应用内消息中心与事件归属

**依赖：** A10；B03。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/notifications.py；api/notification_routes.py；database.py；frontend/src/components/NotificationCenter.jsx。新增测试：`tests/test_notification_recipients.py`；涉及交互时补 `frontend/src/__tests__/notification_recipients.test.jsx`。

**交付／实现：** 持久化收件人、事件、去重键、未读/已读/时间；个人事件本人，系统事件唯一管理员，受全局限制普通用户只见必要说明。未配置外部渠道也能完整使用；接入预算、备份、来源故障/恢复事件。

- [x] 写确定性用例：`test_admin_alert_never_contains_other_user_content：系统与个人消息正确分流，同事件只生成一次，普通用户不能查他人消息`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_notification_recipients.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C06：PushPlus 优先与统一渠道设置

**依赖：** C05。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/notification_adapters/pushplus.py；api/notification_routes.py；frontend/src/components/NotificationSettings.jsx。新增测试：`tests/test_pushplus_channel.py`；涉及交互时补 `frontend/src/__tests__/pushplus_channel.test.jsx`。

**交付／实现：** 支持保存多条配置但仅一个active；默认推荐PushPlus，用户可不配外部渠道。不按事件分别选渠道；密钥掩码，发送摘要，局域网链接说明可达条件。

- [x] 写确定性用例：`test_all_events_use_one_active_channel：个人全部外发事件共用所选渠道，外部失败不换渠道；配置未完成不阻断业务`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_pushplus_channel.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

### C07：邮件、飞书与发送状态机

**依赖：** C06。  **输入：** 已验收依赖接口及需求对应规则。

**文件：** services/notification_adapters/email.py；services/notification_adapters/feishu.py；services/notification_delivery.py；services/feishu_notify.py。新增测试：`tests/test_notification_delivery.py`；涉及交互时补 `frontend/src/__tests__/notification_delivery.test.jsx`。

**交付／实现：** 保留现有飞书作为用户选择；补SMTP安全连接。每条投递冻结渠道版本，provider accepted不等于已读；明确失败有限同渠道重试，超时未知不盲目重发。换渠道只作用于新消息，旧消息手动重发明确确认目标。

- [x] 写确定性用例：`test_unknown_result_and_channel_change_do_not_duplicate：超时不自动重发，旧任务不改投新渠道，停用用户阻止新发送`；使用两个独立用户、固定时钟和伪造外部适配器，不使用真实凭证。
- [x] 实现前运行 `python -m pytest tests/test_notification_delivery.py -q`，确认新断言因目标功能缺失失败，记录原因。
- [x] 按上述边界实现最小闭环，更新受影响调用点与配置说明；遇到原有行为冲突先对照需求，不默默降级。
- [x] 再运行该测试，预期全部通过；有前端改动运行 `npm --prefix frontend test -- --run` 与 `npm --prefix frontend run build`。平台／供应商实测按总计划另记，离线通过不替代真实联调。
- [x] 复核权限、失败与迟到路径，执行相关既有回归；完成一个资源族即检查差异并作小提交（仅执行获授权后），在执行清单记录证据。

## 阶段复核

- [x] 逐项核对离线合同与权限／失败路径，真实账户未核验时不开放搜索。
- [ ] 真实市场覆盖、搜索免费条件、实际通知收件人及完整平台联调验收。
- [x] 检查没有默认身份、费用为零假设、真实凭证测试或对生产数据的隐式恢复。
- [x] 将真实联调、未验证环境和可复现失败分别记录；未满足依赖时不把任务标为完成。

离线完成不代替真实联调；C-V01～C-V04 继续在任务清单追踪，B 的分时计价和完整矩阵不因 C 实施而关闭。
