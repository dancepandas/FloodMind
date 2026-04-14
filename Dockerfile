# RTX 2080 (Turing架构, CUDA 12.4)
# 使用 PyTorch 2.6.0+ 修复 CVE-2025-32434 安全漏洞
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    curl \
    fontconfig \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    fonts-wqy-zenhei \
    libreoffice \
    poppler-utils \
    && fc-cache -fv \
    && rm -rf /var/lib/apt/lists/*

RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    && pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn

ENV PIP_DEFAULT_TIMEOUT=300
ENV PIP_RETRIES=10

COPY requirements.txt .
RUN pip install --no-cache-dir --progress-bar off -r requirements.txt

COPY . .

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

EXPOSE 13014

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:13014/api/health || exit 1

CMD ["python", "web_server.py", "--host", "0.0.0.0", "--port", "13014"]
