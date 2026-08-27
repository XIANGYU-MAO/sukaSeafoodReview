FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd --system review \
    && useradd --system --gid review --home-dir /app --create-home review

WORKDIR /app
COPY requirements.txt ./requirements.txt
ARG SUKASEAFOOD_PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN python -m pip install --disable-pip-version-check --no-cache-dir \
    --index-url "${SUKASEAFOOD_PIP_INDEX_URL}" \
    --requirement requirements.txt

COPY --chown=review:review alembic.ini ./alembic.ini
COPY --chown=review:review alembic ./alembic
COPY --chown=review:review app ./app

USER review
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=15s --retries=5 \
    CMD ["python", "-c", "import json,urllib.request; assert json.load(urllib.request.urlopen('http://127.0.0.1:8000/v1/health', timeout=2)) == {'status':'ok'}"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
