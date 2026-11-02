FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# State dirs are only a fallback: attach a Railway volume (or bind-mount) so they survive redeploys. Do NOT use VOLUME here (Railway rejects it).
RUN mkdir -p /app/sessions /app/data /app/logs
ENV PYTHONUNBUFFERED=1 PORT=3000
EXPOSE 3000
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 CMD curl -fsS http://localhost:${PORT}/ || exit 1
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["python", "-m", "xcore"]
