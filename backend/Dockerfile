# Imagen esqueleto del backend (issue #01).
# La imagen definitiva (usuario no-root, base slim con digest verificado ARM64, fuentes
# para PDF) se define con el issue #26 — decisiones_proyecto.md §14.3.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# El esqueleto FastAPI del issue #07 ya sirve health y errores estándar.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
