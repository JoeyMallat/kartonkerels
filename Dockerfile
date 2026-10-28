FROM litestream/litestream:0.5.17 AS litestream

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    DATA_DIR=/data \
    DATABASE_PATH=/data/kartonkerels.db

WORKDIR /app

COPY --from=litestream /usr/local/bin/litestream /usr/local/bin/litestream
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py backup.py ./
COPY backend ./backend
COPY static ./static
COPY deploy/seed_from_r2.py ./deploy/seed_from_r2.py
COPY deploy/litestream.yml /etc/litestream.yml
COPY deploy/entrypoint.sh /entrypoint.sh

RUN sed -i 's/\r$//' /entrypoint.sh /etc/litestream.yml && chmod +x /entrypoint.sh && mkdir -p /data

EXPOSE 8080
CMD ["/entrypoint.sh"]
