# ScreenerX.ai — FastAPI dashboard
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY data ./data

RUN pip install .

EXPOSE 8000

# Many platforms inject PORT; default 8000 for local docker run -p 8000:8000
# Trust X-Forwarded-* from Caddy/nginx on the same host (fixes OAuth redirect_uri / sessions behind TLS).
CMD sh -c 'exec uvicorn swing.web.app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips="*"'
