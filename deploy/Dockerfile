FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg tesseract-ocr tesseract-ocr-rus \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY afina_watch /app/afina_watch
RUN pip install --no-cache-dir .

COPY configs /app/configs
CMD ["python", "-m", "afina_watch", "--config", "/app/configs/watch.yaml"]
