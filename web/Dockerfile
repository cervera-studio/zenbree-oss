FROM node:24-slim AS base
RUN corepack enable
WORKDIR /app

FROM base AS builder
COPY package.json pnpm-lock.yaml .pnpmfile.cjs ./
RUN pnpm install --frozen-lockfile
COPY . .
# NEXT_PUBLIC_* are inlined at build time.
ARG BETTER_AUTH_IDENTIFY_URL=""
ARG NEXT_PUBLIC_API_URL=""
ARG NEXT_PUBLIC_POSTHOG_KEY=""
ARG NEXT_PUBLIC_POSTHOG_HOST=""
ARG NEXT_PUBLIC_BETTERSTACK_WEB_ERROR_DSN=""
ARG NEXT_PUBLIC_LANDING_URL=""
# Build memory (see next.config.ts): 1 skips the type check, a number caps the page-data workers.
# Only the verify stack sets them.
ARG NEXT_BUILD_SKIP_TYPECHECK=""
ARG NEXT_BUILD_WORKERS=""
ENV BETTER_AUTH_IDENTIFY_URL=$BETTER_AUTH_IDENTIFY_URL \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_POSTHOG_KEY=$NEXT_PUBLIC_POSTHOG_KEY \
    NEXT_PUBLIC_POSTHOG_HOST=$NEXT_PUBLIC_POSTHOG_HOST \
    NEXT_PUBLIC_BETTERSTACK_WEB_ERROR_DSN=$NEXT_PUBLIC_BETTERSTACK_WEB_ERROR_DSN \
    NEXT_PUBLIC_LANDING_URL=$NEXT_PUBLIC_LANDING_URL \
    NEXT_BUILD_SKIP_TYPECHECK=$NEXT_BUILD_SKIP_TYPECHECK \
    NEXT_BUILD_WORKERS=$NEXT_BUILD_WORKERS \
    NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM node:24-slim AS runner
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=builder --chown=node:node /app/.next/standalone ./
COPY --from=builder --chown=node:node /app/.next/static ./.next/static
COPY --from=builder --chown=node:node /app/public ./public
USER node
EXPOSE 3000
CMD ["node", "server.js"]
