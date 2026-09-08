FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim AS app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx curl wget unzip vim-tiny \
    && rm -rf /var/lib/apt/lists/*

RUN wget -nc https://download.oracle.com/java/21/latest/jdk-21_linux-x64_bin.deb && \
    apt install -y ./jdk-21_linux-x64_bin.deb && \
    java -version && \
    rm -f ./jdk-21_linux-x64_bin.deb

RUN wget -nc https://download.oracle.com/otn_software/java/sqldeveloper/sqlcl-latest.zip && \
    unzip -o sqlcl-*.zip && \
    ln -s $PWD/sqlcl/bin/sql /usr/local/bin/sql && \
    sql -V && \
    rm -f ./sqlcl-*.zip

WORKDIR /app/backend

COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY backend/ /app/backend/
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/start.sh /start.sh
COPY --from=frontend-builder /app/frontend/dist /usr/share/nginx/html

RUN chmod +x /start.sh \
    && rm -f /etc/nginx/sites-enabled/default \
    && mkdir -p /var/cache/nginx /var/lib/nginx /var/log/nginx

EXPOSE 80

CMD ["/start.sh"]
