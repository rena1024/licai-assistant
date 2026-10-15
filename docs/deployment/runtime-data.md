# 运行目录与隔离测试（A01）

## 持久数据位置

`LICAI_DATA_DIR` 是数据库、问答图片、落盘缓存、备份和运行配置的统一根目录。
未设置时使用项目目录内的 `runtime-data/`；相对路径按项目目录解析，不随启动终端的位置变化。

```text
runtime-data/
├── portfolio.db
├── media/                  # 问答附件与生成的图片
├── cache/                  # 汇率和基金估值模型
├── backups/                # 定时备份、导入前备份
├── config/
│   └── .env                # 可选：本机凭证和外部服务配置
└── logs/                   # 评测输出；统一运行日志在 D03 接入
```

macOS 原生启动示例（项目根目录、已激活虚拟环境）：

```bash
export LICAI_DATA_DIR="$HOME/Library/Application Support/licai"
python run.py
```

PowerShell 的对应变量语法如下；此处仅说明配置语法，不代表已完成 Windows 实机验收：

```powershell
$env:LICAI_DATA_DIR = Join-Path $env:LOCALAPPDATA 'licai'
.\venv\Scripts\python.exe run.py
```

`config.py` 的 `db_path="portfolio.db"` 继续可用，其含义变为运行目录内的数据库。
显式指定的其他数据库路径也必须在该目录内；工具脚本的 `--db` 使用绝对路径。
评测脚本的相对 `--db` 从运行目录解析，默认使用 `portfolio.demo.db`，缺库立即退出，
不会回退到个人账本。演示脚本也仅在运行目录操作，不会自动填充演示数据。

应用不再自动读取项目根目录的 `.env`；需要的设置应由部署者放到运行目录的 `config/.env`，
或通过进程环境变量提供。进程环境变量优先；`.env` 不能覆盖 `LICAI_DATA_DIR` 和 `LICAI_PROFILE`。
不要在两个位置同时保留不同的有效凭证。Python 的非敏感业务参数仍沿用现有 `config.py`。

## 历史数据保护与兼容边界

- 默认启动发现项目根目录存在非空 `portfolio.db` 时拒绝初始化，显式指定新的空目录后可继续。
- 目标目录中已有非空数据库但缺少运行布局标记时也拒绝初始化；不会清库、自动认领或迁移。
- 新库记录 `runtime_metadata.layout_version`，后续重启可识别；不支持的版本会停止初始化。
- 历史的 `ask_media/`、汇率文件和基金估值缓存不会自动导入新目录。
- 新建目录在 POSIX 系统请求权限 `0700`，新建业务数据库请求 `0600`；既有目录权限和 Windows ACL 仍由部署者管理。
- 默认目录已加入 `.gitignore`。自定义目录应选在代码库外，数据库、图片、凭证和备份均不要提交。

本轮只完成 A01，不代表账号隔离或完整 P0 已交付。旧 `install.sh`/launchd 系统日志路径
尚未迁移，后续 D03/D06 统一；本轮不会安装服务或修改系统设置。
一致性备份、恢复维护态和费用隔离分别由 D01/D02 实现。

## 安全测试入口

```bash
python -m pip install -r requirements-test.txt
python -m pytest tests/test_runtime_paths.py -q
python -m pytest -q
```

`tests/conftest.py` 在业务模块导入前创建独立临时目录，强制使用 `test` profile，
丢弃调用者提供的数据目录与已知外部服务凭证环境变量。旧用例的 `mkstemp` 和 pytest
临时路径也在同一沙箱内，测试结束后清理。每个用例默认获得独立数据库路径。

测试进程默认拒绝 TCP 连接、真实子进程和沙箱外 SQLite 连接（包括 SQLite URI 与软链接）。
外部依赖需在边界使用伪造适配器；不会通过启动真实服务完成回归。
Matplotlib 的缓存也隔离到临时目录。

`test` profile 的应用生命周期只建测试库，不探测代理、不恢复外部配置、不预热行情或启动定时任务；
LLM 客户端不读取 Keychain、不回退到本机 Claude CLI。
网络和子进程保护由 pytest 入口安装，单独设置 `LICAI_PROFILE=test` 不等同于操作系统沙箱，
不能用来隔离任意不可信程序或替代未来的独立诊断 worker。

`evals/run.py` 是明确需要真实工具和模型额度的人工评测入口，不属于上述离线回归命令。
本轮未运行真实模型请求、外发通知或部署操作。
