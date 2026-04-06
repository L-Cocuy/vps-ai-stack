FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
  && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ghostscript \
    qpdf \
    tesseract-ocr \
    tesseract-ocr-eng \
    pngquant \
    unpaper \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt

RUN python -m pip install --upgrade pip==25.0.1 \
  && python -m pip install -r /app/requirements.txt

COPY main.py /app/main.py

RUN useradd --create-home --uid 10001 appuser \
  && chown -R appuser:appuser /app

USER appuser

EXPOSE 8081

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8081"]
