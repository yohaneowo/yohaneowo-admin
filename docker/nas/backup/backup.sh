#!/usr/bin/env bash
# 导出整个 fastapiadmin 数据库到 /backup/fastapiadmin-<时间>.sql.gz，并删掉超过 BACKUP_KEEP_DAYS 天的旧备份。
# 每日由 entrypoint.sh 自动执行；也可以随时手动执行：docker compose exec backup /backup.sh
set -euo pipefail

BACKUP_DIR=/backup
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
name="fastapiadmin-$(date +%Y%m%d-%H%M%S).sql.gz"

mkdir -p "$BACKUP_DIR"
# 先写到临时文件，导出完整才改名：中途失败不会留下看起来正常、其实不完整的备份
tmp="$BACKUP_DIR/.${name}.partial"
trap 'rm -f "$tmp"' EXIT

# --single-transaction：InnoDB 一致性快照，导出期间不锁表，后端照常读写
MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqldump -h mysql -u root \
	--single-transaction --routines --triggers --events --set-gtid-purged=OFF \
	--databases fastapiadmin | gzip > "$tmp"
mv "$tmp" "$BACKUP_DIR/$name"
echo "备份完成：$name（$(du -h "$BACKUP_DIR/$name" | cut -f1)）"

deleted=$(find "$BACKUP_DIR" -maxdepth 1 -name 'fastapiadmin-*.sql.gz' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [[ "$deleted" -gt 0 ]]; then
	echo "已删除 $deleted 个超过 $KEEP_DAYS 天的旧备份"
fi
