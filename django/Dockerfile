FROM python:3.13-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data PORT=8000
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libffi8 \
    fonts-dejavu-core fonts-liberation poppler-utils tesseract-ocr \
    tesseract-ocr-fra tesseract-ocr-eng ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 10001 --create-home app && mkdir -p /data && chown app:app /data \
    && DATA_DIR=/tmp/build-data python manage.py collectstatic --noinput \
    && DATA_DIR=/tmp/build-data python manage.py build_source \
    && chmod +x /app/entrypoint.sh
USER app
ENV XDG_CACHE_HOME=/tmp/zefacture-cache
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn","config.wsgi:application","--bind","0.0.0.0:8000","--workers","2","--threads","2","--timeout","180","--control-socket","/tmp/gunicorn.ctl","--max-requests","1000","--max-requests-jitter","100","--access-logfile","-","--access-logformat","%(h)s %(m)s %(U)s %(s)s %(L)s","--error-logfile","-"]
