# RTX 2080 (Turing架构, CUDA 12.4)
# 使用 PyTorch 2.6.0+ 修复 CVE-2025-32434 安全漏洞
#
# ── 架构基线（forward-only）─────────────────────────────────
# FloodMind 已切到 SDK-first：web/TUI 前端被弃用，desktop 端由独立项目消费
# 本 SDK API。本镜像不再启动 Flask/waitress web 服务，改为承载 SDK + 其
# 依赖的可复现运行环境。运行示例：
#   docker run --rm -it floodmind-sdk \
#       floodmind run "加载某流域降雨 CSV 并预报未来 24h 流量"
# 若团队需要 web 部署，请在新项目里基于本 SDK 包二次开发。
# ──────────────────────────────────────────────────────────
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

WORKDIR /app

# 使用清华镜像源解决 archive.ubuntu.com 无法访问的问题
RUN sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list && \
    sed -i 's|http://security.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    libgl1 \
    libglib2.0-0 \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    libreoffice \
    poppler-utils \
    nodejs \
    npm \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=10

COPY . .
# 安装 SDK + 其 GPU/文档依赖（无 web/tui extras）
RUN pip install --no-cache-dir --progress-bar off ".[deployment]"

RUN npm install -g docx

RUN mkdir -p /app/data/sessions \
    && mkdir -p /app/data/vector_store \
    && mkdir -p /app/data/matplotlib \
    && mkdir -p /app/model_cache \
    && rm -rf /root/.cache/matplotlib \
    && chmod -R 777 /app/data

ENV HF_ENDPOINT=https://hf-mirror.com
ENV HF_HOME=/app/model_cache
ENV DATA_DIR=/app/data
ENV PYTHONPATH=/app
ENV PYTHONIOENCODING=utf-8
ENV MPLBACKEND=Agg
ENV MPLCONFIGDIR=/app/data/matplotlib

# SDK-only：默认入口展示帮助。调用方应在自己的服务里 `from floodmind import Agent`。
# 示例：`docker run --rm floodmind-sdk floodmind run "你的任务"`
CMD ["floodmind", "--help"]
