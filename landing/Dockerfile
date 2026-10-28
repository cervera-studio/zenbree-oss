FROM node:24-slim AS builder
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
ARG PUBLIC_APP_URL=""
ENV PUBLIC_APP_URL=$PUBLIC_APP_URL
RUN pnpm build && sed "s|__APP_URL__|${PUBLIC_APP_URL:-http://localhost:3000}|" nginx.conf > /tmp/nginx.conf

FROM nginxinc/nginx-unprivileged:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY --from=builder /tmp/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
