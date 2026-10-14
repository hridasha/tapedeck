# Stage 1: build the web page
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

# Stage 2: the Python app, with the built page next to it
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 TAPEDECK_DB=/data/tapedeck.db TAPEDECK_WEB_DIR=/app/web_dist TAPEDECK_SERVER_HOST=0.0.0.0
COPY pyproject.toml README.md ./
COPY tapedeck/ tapedeck/
COPY examples/ examples/
RUN pip install --no-cache-dir .
COPY --from=web /web/dist /app/web_dist
VOLUME /data
EXPOSE 8700
# Fake servers and recorders need ports too: publish the range in docker-compose.yml
CMD ["tapedeck", "ui", "--host", "0.0.0.0", "--no-open"]
