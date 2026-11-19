# NetTwin backend: Python 3.11 + FastAPI + Scapy (пассивный сниффер)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

# iproute2  — команда `ip` (адреса, маршруты, JSON-вывод)
# ethtool   — скорость/дуплекс/линк
# libpcap0.8 — компиляция BPF-фильтра для сниффера
# curl      — healthcheck внутри контейнера
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      iproute2 ethtool libpcap0.8 curl ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY frontend ./static

# Cytoscape.js кладём локально в образ — интерфейс работает полностью офлайн.
RUN mkdir -p /app/static/vendor /data \
 && curl -fsSL https://cdn.jsdelivr.net/npm/cytoscape@3.30.2/dist/cytoscape.min.js \
      -o /app/static/vendor/cytoscape.min.js \
 && grep -q -i "cytoscape" /app/static/vendor/cytoscape.min.js

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
