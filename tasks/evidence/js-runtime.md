# 启动修复：MiniRacer/V8 首次并发初始化崩溃

- 日期：2026-10-15；分支：`codex/p0-foundation-protection`。
- 用户报错：`[FATAL:address_pool_manager.cc(67)] Check failed: !pool->IsInitialized()`，调用栈为 `libmini_racer.dylib`、`libffi`、Python 3.13。
- 环境：本机 macOS、Python 3.13.5、AKShare 1.19.1、mini-racer 0.14.1。

## 根因与隔离复现

AKShare 部分行情接口通过 MiniRacer 创建 JavaScript 上下文，项目会通过线程并发调用这些接口。
在独立进程只执行本地 `1 + 2`，不运行应用、不读取数据库、不请求网络，即可复现：

| 冷进程场景 | 结果 |
|---|---|
| 主线程单个上下文 | 正常，结果 3 |
| 4 个线程首次并发创建上下文，共 8 次求值 | 退出码 -5，出现与用户相同断言和动态库偏移 |
| 只调用 `init_mini_racer()` 后并发 | 仍退出 -5，说明仅初始化库不足 |
| 主线程创建、求值并关闭首个上下文，再并发 | 正常 |

本轮没有把非原生异常测试通过当成崩溃修复证据；另外运行了真实原生库的冷进程验证。
复现进程关闭 core dump，未创建任何真实业务数据或凭证。

## 修复

新增 `services/js_runtime.py`，串行创建首个 MiniRacer 上下文、求值常量 `0` 并关闭。
通过锁和成功标记保证进程内只预热一次；失败不标记成功，异常正常传递。
使用的公共上下文接口见 [PyMiniRacer 官方文档](https://bpcreech.com/PyMiniRacer/api/)。

`run.lifespan` 在测试模式早退之后、初始化凭据/任何 yield/个人后台任务之前调用此预热，
使请求和行情线程运行前已经完成原生地址池的首次分配。
复用已有 AKShare 传递依赖，将实际验证的 `mini-racer==0.14.1` 显式记录在 requirements；
没有安装新版本或升级其他依赖。数据库目录和业务逻辑不变。

## 验证

- RED：`venv/bin/python -m pytest tests/test_js_runtime.py -q` → 2 项失败，启动顺序缺少预热且辅助模块不存在。
- GREEN：`venv/bin/python -m pytest tests/test_js_runtime.py tests/test_account_bootstrap.py -q` → 26 passed，2.73s。
- 真实原生库：5 个全新子进程各调用两次初始化辅助函数，再以 4 线程执行 64 次上下文求值，320 次全部正确，5 个进程退出码全部为 0。
- 全量：`venv/bin/python -m pytest -q` → 803 passed，16 条既有弃用警告，10.14s。
- `git diff --check` 通过。测试模式仍不执行生产启动预热、代理探测或后台任务。

原生冷进程验证是本次明确执行的离线诊断，不改变 pytest 默认禁止真实子进程的安全夹具。
未启动生产服务、未修改运行数据库、未发起真实行情/模型/通知请求。
本轮未做 Windows 或其他 Python/MiniRacer 版本实测，不将结果扩展成平台保证。
