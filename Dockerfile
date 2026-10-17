# syntax=docker/dockerfile:1
# 候选补丁版本的官方来源与未验证项见 docs/deployment/macos-docker.md。
FROM node:22.23.3-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12.15-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LICAI_DATA_DIR=/var/lib/licai \
    MPLCONFIGDIR=/var/lib/licai/cache/matplotlib \
    XDG_CACHE_HOME=/var/lib/licai/cache \
    HOME=/var/lib/licai
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 licai \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /var/lib/licai licai \
    && mkdir -p /var/lib/licai \
    && chown 10001:10001 /var/lib/licai
# 沿用图表代码已有的Linux字体路径，避免中文标签缺字。
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-wqy-microhei \
    && rm -rf /var/lib/apt/lists/*
# AKShare的旧发行包共享模块目录；最后重装锁定引擎，避免旧API覆盖。
RUN pip install --no-cache-dir --force-reinstall --no-deps mini-racer==0.14.1
COPY run.py database.py models.py ./
# 只带无凭证模板；宿主机的个性化配置不进入镜像。
COPY config.example.py ./config.py
COPY api/ ./api/
COPY services/ ./services/
# 公开席位名录属于应用资源，不包含业务数据。
COPY data/seat_names.json ./data/seat_names.json
COPY scripts/admin_recovery.py ./scripts/admin_recovery.py
COPY --from=frontend /build/static ./static
# 清除构建工具留下的root缓存，确保新卷内所有目录都由运行账号拥有。
RUN rm -rf /var/lib/licai/cache \
    && mkdir -p /var/lib/licai/cache/matplotlib \
    && chown -R 10001:10001 /var/lib/licai \
    && chmod -R go-rwx /var/lib/licai
USER 10001:10001
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8888/api/health', timeout=3).close()"

CMD ["python", "-m", "uvicorn", "run:app", "--host", "0.0.0.0", "--port", "8888", "--workers", "1"]
