# 运行日志与临时 Trace 基础

`services/logging_config.py` 提供受限结构化运行事件，不接管标准输出，不收集历史 `print`、第三方日志或任意异常堆栈。本模块不提供原始日志浏览 API；现有代码中尚未迁移的输出不因此获得统一脱敏保证。管理员鉴权、配置审计和定期清理调度由直接调用点接入，不能仅凭模块存在宣称全部 D03 已完成。

## 事件边界

`record_event(event=..., outcome=..., elapsed_ms=..., actor_id=None, trace=False)` 只接受白名单事件代码、`success/failure/denied` 结果、有限非负耗时毫秒和内部整数操作者 ID。没有 `message`、异常对象、任意 metadata、提示词、答案、网址或凭证参数。必须把失败映射为固定事件与结果，再记录；不要将 `str(exception)` 拼入任何字段。

当前事件代码：`health_check`、`startup`、`shutdown`、`backup_created`、`backup_failed`、`restore_completed`、`restore_failed`、`retention_completed`、`retention_failed`、`trace_enabled`、`trace_disabled`、`runtime_unavailable`。扩展事件需修改白名单并补测试，不能由用户输入动态定义代码。

日志位于 `LICAI_DATA_DIR/logs/events-YYYY-MM-DD.jsonl`，按 UTC 日期分文件，新文件 POSIX 权限 `0600`，目录 `0700`。写入失败向调用者抛出，不能视为记录成功。应用日志与 SQLite 审计分开保存；本模块不改变审计保留策略。

## Trace 与保留策略

- `set_trace(True)` 将明确到期时间 `trace_until=当前时间+1800秒` 合并写入运行状态；`set_trace(False)` 写入0。只有显式开启会改变到期时间，读取与重启不续期。
- `trace_status()` 返回 `enabled`、`trace_until`；到期时 `enabled=False`，历史到期时间可保留供核对。默认关闭，`trace=True` 的事件在关闭／到期时不写入并返回 False。普通结构事件不受开关影响。
- `safe_metadata()` 只返回 Trace 开关、到期时间和运行日志保留天数，不返回运行目录、凭证、维护状态或任何日志正文。
- `set_log_retention_days(days)` 独立持久化运行日志保留天数，默认7，允许1～3650的整数。与审计默认180天的配置分开，修改日志设置不会重置维护代际或费用隔离。
- `cleanup_logs()` 只删除 logs 第一层、名称精确匹配 `events-YYYY-MM-DD.jsonl` 且早于保留期的普通文件；今天及前6天默认保留。不递归、不删除未知文件、不跟随文件／目录软链接，logs 本身是软链接时拒绝。应用生命周期中的维护循环每5分钟调用清理；模块不暗中启动线程。

固定时钟测试入口：`venv/bin/python -m pytest tests/test_audit_privacy.py -q`。这些测试覆盖模拟密钥／正文异常不落盘、Trace 到期与重载、状态合并、保留边界和软链接；不证明全部既有标准输出已脱敏。
