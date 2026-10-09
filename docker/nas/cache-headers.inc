# 静态资源长缓存 + 隐藏文件防护。被 nginx.conf 的 /web、/app location include。
# 撤销：删除本文件 + 移除 nginx.conf 中对应 include 行，然后重建 nginx。
location ~ /\. {
    deny all;
    access_log off;
    log_not_found off;
}
location ~* ^/(web|app|docs)/(js|css|assets|static)/ {
    add_header Cache-Control "public, max-age=31536000, immutable";
    access_log off;
}
location ~* ^/(web|app|docs)/[^/]*\.(?:png|jpe?g|gif|svg|webp|ico|woff2?|ttf|eot)$ {
    add_header Cache-Control "public, max-age=2592000";
    access_log off;
}
