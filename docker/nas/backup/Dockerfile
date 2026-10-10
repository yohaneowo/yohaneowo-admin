# yohaneowo-admin 的数据库备份服务（NAS 用）：每日自动导出、可手动备份与还原，并维护只读账号。
# 以 MySQL 官方镜像为基础，里面已有 mysqldump、mysql、gzip。构建上下文是 docker/nas/backup/。
FROM mysql:8.0

ENV TZ=Asia/Shanghai

COPY --chmod=755 entrypoint.sh backup.sh restore.sh /

ENTRYPOINT ["/entrypoint.sh"]
