"""打包文件的离线契约；不冒充 Docker 实际构建／重建验收。"""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_paths_and_health_contract():
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.is_file(), "缺少最小容器打包"
    content = dockerfile.read_text()
    assert "FROM node:22.23.3-bookworm-slim AS frontend" in content
    assert "FROM python:3.12.15-slim-bookworm AS runtime" in content
    assert "npm ci" in content and "npm run build" in content
    assert "COPY --from=frontend /build/static ./static" in content
    assert "COPY config.example.py ./config.py" in content
    assert "COPY scripts/admin_recovery.py ./scripts/admin_recovery.py" in content
    assert "COPY data/seat_names.json ./data/seat_names.json" in content
    assert "fonts-wqy-microhei" in content
    assert "--no-install-recommends" in content
    assert "pip install --no-cache-dir --force-reinstall --no-deps mini-racer==0.14.1" in content
    assert "COPY . " not in content and "COPY config.py" not in content
    assert "USER 10001:10001" in content
    assert "chown -R 10001:10001 /var/lib/licai" in content
    assert "rm -rf /var/lib/licai/cache" in content
    assert 'CMD ["python", "-m", "uvicorn", "run:app"' in content
    assert '"--workers", "1"' in content
    assert "LICAI_DATA_DIR=/var/lib/licai" in content
    health = content.split("HEALTHCHECK", 1)[1].split("\n\n", 1)[0]
    assert "http://127.0.0.1:8888/api/health" in health
    assert "urllib.request.urlopen" in health
    assert "11434" not in health and "api_key" not in health


def test_compose_mounts_only_persistent_directory():
    path = ROOT / "compose.yaml"
    assert path.is_file(), "缺少 Compose 配置"
    content = path.read_text()
    assert "restart: unless-stopped" in content
    assert "licai-data:/var/lib/licai" in content
    assert content.count("- licai-data:/var/lib/licai") == 1
    assert "127.0.0.1:8888:8888" in content
    assert "env_file:" not in content
    assert "docker.sock" not in content
    assert "privileged:" not in content and "network_mode: host" not in content
    assert "./:" not in content


def test_build_context_excludes_local_state_and_credentials():
    path = ROOT / ".dockerignore"
    assert path.is_file(), "缺少构建上下文排除规则"
    rules = path.read_text().splitlines()
    assert "*" in rules
    for required in ("!run.py", "!database.py", "!models.py", "!config.example.py", "!api/**", "!services/**", "!frontend/**", "!scripts/admin_recovery.py"):
        assert required in rules
    for excluded in ("**/.env", "**/.env.*", "**/*.db", "**/*.db-*", "**/*.sqlite*", "**/backups", "**/node_modules", "**/__pycache__", "frontend/dist", "config.py"):
        assert excluded in rules
        assert rules.index(excluded) > rules.index("!frontend/**")
    assert "!data/seat_names.json" in rules
    assert "!data/**" not in rules
    assert not re.search(r"^!(?:runtime-data|backups|\.git|\.env|config\.py)(?:/|$)", "\n".join(rules), re.M)
