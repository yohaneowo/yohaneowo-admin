# Frontend + nginx image for Coolify. Unlike docker/nas/web.Dockerfile (which expects a dist/ built
# on the PC by publish.sh), this builds the Vue app inside the image, so Coolify can build it
# straight from the repo. Build context is the repo root; web.Dockerfile.dockerignore applies.
FROM node:22-alpine AS build
RUN corepack enable
WORKDIR /src
COPY frontend/web/package.json frontend/web/pnpm-lock.yaml frontend/web/.npmrc ./
RUN pnpm install --frozen-lockfile
COPY frontend/web/ ./
# Same-origin deployment: the API is reached by relative path (/api/v1 from .env), SSE on the page's origin.
RUN printf 'VITE_APP_ENV = prod\nVITE_APP_TITLE = yohaneowo-admin\nVITE_API_BASE_URL =\nVITE_APP_WS_ENDPOINT =\nVITE_DROP_CONSOLE = true\n' > .env.production \
 && pnpm build:prod

FROM nginx:1.25-alpine
ENV TZ=Asia/Shanghai
COPY docker/nas/nginx.conf /etc/nginx/nginx.conf
COPY docker/nas/cache-headers.inc /etc/nginx/cache-headers.inc
COPY --from=build /src/dist /usr/share/nginx/html/web/dist
EXPOSE 80
