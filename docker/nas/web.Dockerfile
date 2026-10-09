# yohaneowo-admin 前端 + Nginx 镜像（NAS 用）。
# 前端在电脑上先 build（docker/nas/publish.sh 会做），这里只把 dist 和 nginx 配置装进去，
# NAS 上不需要源码或 Node。构建上下文是 docker/nas/（publish.sh 会把 dist 复制到 ./dist）。
FROM nginx:1.25-alpine

ENV TZ=Asia/Shanghai

COPY nginx.conf /etc/nginx/nginx.conf
COPY cache-headers.inc /etc/nginx/cache-headers.inc
COPY dist /usr/share/nginx/html/web/dist

EXPOSE 80
