FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=8000

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.tencent.com/pypi/simple \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /app app

COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
RUN chmod +x ./scripts/start.sh ./scripts/health_check.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["./scripts/health_check.sh"]

ENTRYPOINT ["./scripts/start.sh"]
