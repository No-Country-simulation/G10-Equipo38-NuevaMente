# Imagen de Streamlit (#26), decisiones_proyecto.md §14.3.
FROM python:3.11.17-slim-bookworm@sha256:0a310eeecf4e1f5a0743f9a6520c90c88d089c903ca5fd283f501e3a805f5f89

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 nuevamente \
    && useradd --uid 10001 --gid 10001 --no-create-home nuevamente
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY components/ ./components/
COPY i18n/ ./i18n/
COPY pages/ ./pages/

COPY .streamlit/ .streamlit/
COPY app.py entrypoint.py ./
ENV HOME=/tmp \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none
USER 10001:10001
EXPOSE 8501

CMD ["python", "entrypoint.py"]
