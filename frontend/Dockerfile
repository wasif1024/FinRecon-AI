FROM node:24-slim AS build
RUN corepack enable
WORKDIR /app
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY . .
RUN pnpm build

FROM node:24-slim
WORKDIR /app
ENV NODE_ENV=production HOSTNAME=0.0.0.0 PORT=3000
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
COPY --from=build /app/public ./public
RUN mkdir -p /app/logs && chown -R node:node /app/logs
USER node
EXPOSE 3000
# Server output goes to the container log and to ./logs/frontend.log (mounted), like the API's logs.
CMD ["sh", "-c", "node server.js 2>&1 | tee -a /app/logs/frontend.log"]
