FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# reportlab and Pillow need these; curl backs the container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libjpeg62-turbo-dev zlib1g-dev libfreetype6-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# The app never needs to write to its own image; running as root would hand an
# RCE in a PDF library full control of the container.
# The local storage backend writes here. Creating it in the image with the right
# owner means a named volume mounted over it inherits that ownership, so the
# non-root process can write to it.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data/storage \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
