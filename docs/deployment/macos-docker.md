# macOS Docker 最小打包（D04 候选）

这是批次 A 的打包入口，沿用 FastAPI、SQLite 和现有 React 前端，无新增应用依赖。只运行一个 Python 应用 worker；不支持扩容多个实例共享同一数据卷。A01～A10、D01 和基础恢复验证通过，并完成下列真实重建验证以后，才可作为日常使用环境。D06 自启动、Windows 原生、手机和完整发布验收仍分别验证。

## 版本与当前证据

| 项目 | 固定候选 | 官方来源 | 当前验证 |
|---|---|---|---|
| Python | `3.12.15-slim-bookworm` | [Python 3.12.15 发布说明](https://www.python.org/downloads/release/python-31215/)、[官方镜像标签清单](https://github.com/docker-library/official-images/blob/master/library/python) | 实际Python3.12.15；构建、启动、重建／恢复及Linux回归通过 |
| Node | `22.23.3-bookworm-slim` | [Node 22.23.3 LTS 发布说明](https://nodejs.org/en/blog/release/v22.23.3)、[官方镜像标签清单](https://github.com/docker-library/official-images/blob/master/library/node) | 构建基底满足Node22>=22.12；前端构建及最终镜像通过，未独立执行node --version |

来源核对日期为 2026-10-16。Python 发布于 2026-09-30，Node 发布于 2026-09-23；这些是候选补丁版本，不是已完成整体验收的版本。Dockerfile 固定补丁标签，但标签并非不可变摘要；后端 requirements.txt 延续现有版本范围，因此尚不承诺逐字节可复现。正式验收应记录拉取后的镜像 digest、实际 Python/Node 版本和依赖版本，再锁定经验证的镜像产物。

2026-10-17实测：Docker客户端／服务端29.7.2、Compose5.5.0、darwin/arm64、desktop-linux；最终镜像运行Linux aarch64、UID10001。构建、production空库启动、独立卷双用户隔离、容器重建持久化及管理员HTTP受保护恢复通过。验证容器禁外网、未发布端口，仅挂专属合成数据卷；没有部署日常服务或使用现有业务数据。完整记录和镜像ID见[D04证据](../../tasks/evidence/d04.md)。

## 数据与配置边界

Compose 只挂载一个命名卷 `licai-data` 到 `/var/lib/licai`，对应 `LICAI_DATA_DIR`。数据库、媒体、备份、日志和运行配置都在此目录下。新卷从镜像预建目录继承 UID/GID 10001 的权限；已有卷权限不匹配时会写入失败，应先停机核对备份和权限，不要改为 root 运行来掩盖问题。

镜像采用前端多阶段构建，运行层只有 Python、后端和构建后的静态文件。运行层增加Debian字体包`fonts-wqy-microhei`，复用现有图表的Linux中文字体路径；不引入新Python／前端库。[官方文件清单](https://packages.debian.org/bookworm/all/fonts-wqy-microhei/filelist)已核对，实际中文码点覆盖及图表标签差异已通过容器验证。应用以 UID/GID 10001 运行，不挂载源码、Docker socket 或宿主凭证。构建上下文使用允许列表，排除 `.env`、数据库、备份和本地 `config.py`；镜像内的 `config.py` 来自 `config.example.py`。AKShare旧发行包与新JS引擎共用模块名；Dockerfile在最后重新安装锁定的mini-racer0.14.1，并清除构建缓存、修正运行目录归属。不要删除这些步骤，否则可能启动失败或非root缓存不可写。修改运行配置时使用持久目录 `config/.env` 或已实现的管理员设置，不把凭证写入 Dockerfile、Compose 或构建参数。

## 构建、启动与停止

前提：用户自行准备 Docker Desktop 和 Compose v2；本指南不自动安装或更改系统设置。以下命令需在项目根目录执行，启动前确认上文门槛。首次启动会建空库，不自动写演示资产。

```sh
docker compose config --quiet
LICAI_IMAGE_TAG=reviewed-a docker compose build
LICAI_IMAGE_TAG=reviewed-a docker compose up -d
docker compose ps
docker compose logs --tail=100 app
curl --fail http://127.0.0.1:8888/api/health
```

健康接口无需登录，只检查应用运行和持久目录／数据库基本健康，不查询模型或发送通知。HTTP 200 表示基本健康，503 表示未就绪；它不证明行情、模型、备份恢复等业务验收通过。Docker healthcheck 失败会标为 unhealthy，`unless-stopped` 只处理进程退出后的重启，不会因 unhealthy 自动重启。

本机打开 `http://127.0.0.1:8888`。默认只发布到本机回环地址。手机局域网访问属于后续验证：确认访问需求及网络边界后再显式修改端口绑定，不自动放开防火墙或公网映射。

```sh
docker compose stop
docker compose start
docker compose down
```

`stop`／`down` 保留命名卷。不要执行 `docker compose down -v`，这会删除持久数据。保持项目名称 `licai`，变更项目名可能得到另一个空卷，应先核对卷而不是重新初始化。

## 管理员初始化与本机密码恢复

应用首次启动只输出初始化凭据的文件位置。可以在部署机器执行以下命令生成／轮换凭据，再由有本机权限的管理员读取命令输出所指文件；不要将凭据写入工单、截图或共享日志。

```sh
docker compose exec app python scripts/admin_recovery.py bootstrap-token
docker compose exec app python scripts/admin_recovery.py bootstrap-token --renew
```

按[账号说明](accounts.md)使用一次性凭据创建唯一管理员、登录后创建普通用户。密码遗失时在本机交互输入新密码，参数中不传密码：

```sh
docker compose exec app python scripts/admin_recovery.py recover
```

恢复密码保留业务数据，撤销旧会话并要求重新登录。缺少 AI 配置不阻止手工理财；批次 A 的云端和未迁移 AI 入口仍保持关闭。

## 宿主机原生 Ollama

Ollama 保留 macOS 原生运行。容器访问宿主机使用 `http://host.docker.internal:11434`，不能使用容器里的 `localhost`。见 [Docker Desktop 宿主机网络说明](https://docs.docker.com/desktop/features/networking/networking-how-tos/)。本 Compose 不启动 Ollama，也不自动配置或调用模型。

待受控模型服务配置接口交付后，在管理员服务配置中填写该地址并完成本地端点验证。当前批次不通过旧环境变量绕过 A10 调用门禁。地址解析不保证 Ollama 监听范围允许访问；如不可达，应由用户检查原生 Ollama 的监听配置，随后按明确授权做单独连接测试。Ollama 晚就绪、自启动和恢复重连属于 D06 实测，不能从健康接口推断成功。

## 升级与回退

1. 在管理员功能中完成 D01 的一致性备份，核对备份清单、摘要和可恢复性，并另存离机副本；不要在线复制 SQLite 主文件充当备份。
2. 记录当前镜像 ID／digest、应用提交、数据库 schema 版本和备份标识。先在隔离副本验证新镜像，确认 schema 兼容策略。
3. 保留旧镜像，停止应用，再用新的唯一 `LICAI_IMAGE_TAG` 构建并启动；复用同一卷。检查健康、登录、两个用户的数据与配置和备份。
4. 升级失败先停服务、保护当前数据。旧镜像只有经兼容确认才能连接当前 schema；不能直接启动旧镜像盲写新版库。需要恢复时按 D02 的维护态校验恢复流程执行，撤销会话、中断旧任务；费用隔离待 B02 接入。不能直接覆盖数据库文件后继续云端消费。

## 必须补做的真实重建验收

在独立 Compose 项目和全新测试卷中执行，不挂生产卷、不用真实凭证、不调用模型或通知。项目名固定使用独立值（例如每条命令都加 `-p licai-d04-verification`），检查解析结果中的卷名后再操作。

1. 记录 macOS、CPU、内存、Docker/Compose、镜像 digest、容器 Python 与构建 Node 版本；完成 `build`，确认依赖安装与前端输出成功。
2. 启动空库，确认没有演示资产；初始化管理员和两个普通用户，各自录入不同资产与配置，验证隔离。
3. 记录资产／配置和媒体，先备份，再停止、删除并重建应用容器，复用测试卷。重登两用户，确认记录、媒体、配置和备份仍在。
4. 核对镜像和构建上下文没有 `.env`、本机数据库或备份，容器 UID 为 10001，仅一个应用 worker，只挂测试数据卷；确认匿名健康 200／维护时503，无模型／通知调用。
5. 测试新版 schema 与旧镜像拒绝／安全恢复路径；记录真实结果。测试卷清理必须先核实项目与卷名，避免误删日常数据。

2026-10-17已完成本批最小构建／启动／双用户／媒体／重建／恢复／权限验收；验证配置去除了端口和外网，日常部署与联网业务需另行验证。完整schema升级回退、Ollama、自启动及其他平台仍未验证，AC-01与D04整体验收保持待完成。
