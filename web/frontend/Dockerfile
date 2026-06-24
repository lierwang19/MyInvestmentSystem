# Build stage
FROM node:20-alpine AS build-stage

# Build-time proxy (for npm install only) — uses host.docker.internal to reach host proxy
ENV http_proxy=http://host.docker.internal:30810 https_proxy=http://host.docker.internal:30810

WORKDIR /app
COPY web/frontend/package*.json ./
RUN npm ci
COPY web/frontend/ .
RUN npm run build

# Production stage
FROM nginx:stable-alpine AS production-stage
COPY --from=build-stage /app/dist /usr/share/nginx/html
COPY web/frontend/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
