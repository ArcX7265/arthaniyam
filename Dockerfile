FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/backend \
    ARTHANIYAM_DATABASE_PATH=/data/arthaniyam.sqlite3 \
    RAZORPAY_MODE=simulate \
    POLICY_COMPILER_MODE=reference

WORKDIR /app

COPY backend/pyproject.toml /app/backend/pyproject.toml
COPY backend/app /app/backend/app
RUN pip install --no-cache-dir /app/backend

COPY frontend /app/frontend
COPY scripts /app/scripts

RUN addgroup --system arthaniyam \
    && adduser --system --ingroup arthaniyam arthaniyam \
    && mkdir -p /data \
    && chown -R arthaniyam:arthaniyam /app /data

USER arthaniyam

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
