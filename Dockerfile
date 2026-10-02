FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY aquaagro ./aquaagro
COPY data ./data
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/state && chown appuser:appuser /app/state
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1
CMD ["uvicorn", "aquaagro.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
