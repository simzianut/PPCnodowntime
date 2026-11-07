# syntax=docker/dockerfile:1
# Build context: repo root (see docker-compose.yml).
FROM node:20-alpine AS build
WORKDIR /repo/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
# mock.ts imports ../../../data/seed (open data, not licensed)
COPY data/seed /repo/data/seed
COPY frontend/ .
# Empty = same origin: REST on /api, WebSocket on /ws (decision 0066).
ENV VITE_API_URL=""
RUN npm run build

FROM nginx:1.27-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /repo/frontend/dist /usr/share/nginx/html
