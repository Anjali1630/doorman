# NOTE: same caveat as backend/Dockerfile - written and reviewed, not
# runtime-validated with `docker build` (no Docker daemon in the build
# sandbox). See README "Known limitations".
FROM node:20-slim AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# nginx (not the dev server) so /api/* can be proxied through to the
# backend container the same way Vite's dev proxy does in local dev.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 5173
