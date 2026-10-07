# Imagen del backend (#26), decisiones_proyecto.md §14.3.
# Índice oficial multiarch: Python 3.11.17, amd64 y arm64.
FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fuentes Unicode y bibliotecas nativas de renderizado para las exportaciones PDF.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        fonts-dejavu-core fontconfig libfreetype6 libjpeg62-turbo libpng16-16 libopenjp2-7 \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 nuevamente \
    && useradd --uid 10001 --gid 10001 --no-create-home nuevamente \
    && mkdir -p /app/.data \
    && chown nuevamente:nuevamente /app/.data
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

ENV HOME=/tmp
USER 10001:10001
EXPOSE 8000
# Un solo proceso Uvicorn mantiene el único worker y escritor SQLite/Chroma.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
