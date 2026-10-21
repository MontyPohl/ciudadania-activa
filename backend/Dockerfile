FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.prod

RUN apt-get update && apt-get install -y --no-install-recommends \
    binutils \
    gdal-bin \
    libgdal-dev \
    libgeos-dev \
    libproj-dev \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/prod.txt

COPY . .

RUN adduser --disabled-password --no-create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

# gthread/4 queda para otra entrega, después de la prueba de estabilidad prolongada.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
