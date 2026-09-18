# Imagen esqueleto del frontend (issue #01).
# La imagen definitiva llega con el issue #26 — decisiones_proyecto.md §14.3.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY components/ ./components/
COPY i18n/ ./i18n/
COPY pages/ ./pages/

# Sin punto de entrada aún: app.py llega con el issue #15.
CMD ["python", "-c", "print('frontend: esqueleto importable (app.py llega con el issue #15)')"]
