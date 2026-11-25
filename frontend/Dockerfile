# OpsPilot AI frontend image: build the Vite app, then serve the static files with nginx.

FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Public, build-time settings only (they end up in browser JavaScript). Never pass secrets here.
ARG VITE_API_BASE_URL=http://localhost:8000
ARG VITE_API_TIMEOUT_MS=10000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL \
    VITE_API_TIMEOUT_MS=$VITE_API_TIMEOUT_MS
RUN npm run build

FROM nginx:1.28-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1:8080/ || exit 1
