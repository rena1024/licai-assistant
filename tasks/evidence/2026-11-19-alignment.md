# 2026-11-19 需求、代码与提交对齐

## 核对口径

本轮起点为 `1113d82`，当前分支为 `codex/p0-foundation-protection`。用户授权先对齐需求和总清单，再按独立功能提交，最后继续原计划验收。保留已有改动，不推送、不部署、不修改日常数据库或系统设置。

原 PRD v0.40 是历史基线；下列增补登记代码已经实现的行为及其依据，不把实现存在等同于验收完成。2026-11-18／19 子计划中的测试和运行实例记录是历史记录，本次复验另记。A／B／C／D 原编号不重排。

## 需求增补与提交边界

| 编号 | 已有行为／变更 | 原任务及依据 | 独立验证 |
| --- | --- | --- | --- |
| R01 | 官方模型 HTTPS 端点兼容代理 Fake-IP；自定义端点继续固定已校验 IP | B04/B08，`llm_runtime._connection_base` | `test_llm_transport.py` |
| R02 | 问答保留最终总结轮次，服务默认输出额度、预算／上下文／能力错误可读 | B06/B08，`stock_agent`、`llm_client`、`ai_gate` | `test_agent_round_limit.py`、`test_ask_output_defaults.py`、`test_ask_error_messages.py`、`test_ai_gate_readiness.py` |
| R03 | 请求级实际模型状态；切页不取消共享任务；六个页面分析按北京时间每日复用，手动重算 | B05/B07，11-19 每日 AI 子计划 | `test_llm_activity.py`、`test_ai_jobs.py`、`test_daily_ai_jobs.py`；`llm-status.mjs`、`daily-ai.mjs` |
| R04 | 早盘简报改为每日北京时间 09:00（含周末），独立调度、不补跑；失败明确提醒 | B07/C05，`scheduler.briefing_tick` 及通知使用说明 | `test_scheduled_briefing.py`、`briefing-feedback.mjs` |
| R05 | 通知批量已读／删除，保留事件去重，发送中禁止删除 | C05/C07，`notifications` | `test_notification_bulk_read.py`、`test_notification_delete.py`、`notification-center.mjs` |
| R06 | 管理入口与个人设置分开，预算读回、保存反馈、搜索条件提示、个人凭证删除 | A10/B01/B03/C04；部署说明 | `admin_navigation`、`usage_retention`、`search_quota` 前端测试；`model-settings.mjs`；`test_search_settings_errors.py` |
| R07 | 停用普通账号后确认用户名删除个人数据，匿名费用关联保留，文件清理可重试 | A10/D02/D03；2026-11-19 用户确认变更 Q02-c/AC-42，保留删除功能 | `test_user_deletion.py`；`model-settings.mjs --real-api --user-deletion` |
| R08 | A 股默认费率来自配置；默认券商模板调整 | A04，`broker_fee_resolver`／`DEFAULT_BROKERS` | `test_broker_fees.py` |
| R09 | 股票历史导入默认只读，写入前备份、冲突拒绝；A 股成本扣减实际入账股息且只计一次 | A04/A08；README、映射示例 | `test_import_stock_history.py`、`test_position_ledger.py` |
| R10 | 基金历史导入、含费移动加权平均成本、剩余持仓分红与累计收益避免重复 | A04/A08；11-18 基金导入／平均成本／持有收益子计划 | `test_import_fund_history.py`、`test_fund_average_cost.py`、`test_fund_holding_dividend.py`；`fund-average-cost.mjs` |
| R11 | 基金现金分红补录、连续录入 | A04；11-18 分红子计划 | `test_fund_dividend.py`、`fund-dividend.mjs` |
| R12 | 基金确认赎回按净到账金额计算，显式零手续费；日期输入和持仓排序／操作栏修复 | A04；现有 API、前端确认函数和测试 | `test_fund_confirmation.py`；`fund_confirmation`、`portfolio_sort` 前端测试；`lot-date.mjs` |

R03 的每日策略只影响已有六个分析入口，不新增每日全市场 AI 报告。R07 不承诺擦除历史备份，恢复旧备份可能恢复旧账号。上述功能有共享文件时按差异块暂存；共享基础设施先于调用方提交。历史导入与账本规则强关联时合并为同一功能提交，不按前后端机械切分。

## 原计划剩余项

| 项目 | 本次核对结论／下一步 | 验收限制 |
| --- | --- | --- |
| B02-F01 | 当前仅固定价格；用户确认保留分时要求，整理提交后继续实施 | 不能以高峰固定价冒充分时支持 |
| B-V01/B-V02 | 完整厂商／场景矩阵未通过 | 需要可用服务；真实付费请求另需授权 |
| C-V01 | 真实行情符号、时刻及成交单位仍待核验 | 离线适配器测试不等同真实行情 |
| C-V02 | 免费搜索实际账户条件尚未核验 | 需实际账户证据，缺证据保持禁用 |
| C-V03/C-V04 | 真实收件人及完整交付未验收 | 需明确接收人及外发授权 |
| D02 | 预算、搜索、通知及删除账号恢复联动已有实现 | 完整平台恢复仍未验收 |
| D03 | 基础审计、日志和 Trace 已有；旧 print／第三方输出尚未全面收口 | 保留缺项，不把白名单日志等同全量脱敏 |
| D04 | 历史最小 Docker 重建已验收 | 当前完整升级及宿主 Ollama 尚待验证 |
| D05 | 预定义隔离回归 worker 和诊断界面尚未实现 | 不以现有 pytest 或维护页替代 |
| D06/D07 | Mac 登录后启动／Ollama 恢复、Windows 原生完整验收缺失 | 本机不能证明 Windows 或真实注销登录行为 |
| D08 | 缺 47 项 AC 逐项证据表及当前全量复验 | 手机浏览器真机、平台和外部联调须独立记录 |

## 本轮验证与提交记录

- 基线复验：后端 `venv/bin/python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning`：1076 passed，16 条弃用警告；前端 `npm --prefix frontend test`：143 passed；临时目录构建通过，存在原有字体导入顺序和包体积警告。
- `daily-ai.mjs`：六入口切页复用、手动生成、失败保留和北京时间跨日通过。其余浏览器及新增验收结果继续追加。
- 构建输出写入临时目录，不覆盖日常实例静态资源。
- `frontend/package-lock.json` 为原有无关改动，保留未提交。
- `outputs/` 含个人财务工作簿及预览，不提交；配置、数据库、备份同样不纳入。
