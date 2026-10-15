FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MINGSHAI_DATA_DIR=/data TZ=Asia/Shanghai
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY mingshai ./mingshai
COPY eval ./eval
RUN mkdir -p /data
EXPOSE 8080
# 数据快照、台账、保存的策略、AI 额度账本都写在 /data（Railway 挂持久卷）；大模型 Key 首次启动时搬进 /data/llm.key
CMD ["sh", "-c", "uvicorn mingshai.app:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
