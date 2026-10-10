#!/usr/bin/env bash
# 用某个备份文件还原整个 fastapiadmin 数据库（会覆盖现有数据）。
#   docker compose exec backup /restore.sh                          # 列出可用的备份
#   docker compose stop backend web                                 # 还原前先停后端，避免边还原边写入
#   docker compose exec backup /restore.sh fastapiadmin-20261010-040000.sql.gz
#   docker compose start backend web
set -euo pipefail

BACKUP_DIR=/backup

if [[ $# -ne 1 ]]; then
	echo "用法：/restore.sh <备份文件名>"
	echo "可用的备份（新的在前）："
	ls -1t "$BACKUP_DIR"/fastapiadmin-*.sql.gz 2>/dev/null | xargs -r -n1 basename || true
	exit 1
fi

file="$BACKUP_DIR/$(basename "$1")"
if [[ ! -f "$file" ]]; then
	echo "找不到 $file"
	exit 1
fi

echo "即将用 $(basename "$file") 覆盖整个 fastapiadmin 数据库。"
echo "建议先执行 /backup.sh 留一份现在的数据。"
read -r -p "确定要还原吗？输入 yes 继续：" answer
if [[ "$answer" != "yes" ]]; then
	echo "已取消"
	exit 1
fi

# 备份是用 --databases 导出的，内含 DROP/CREATE 表的语句，直接导入即可覆盖
gunzip -c "$file" | MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h mysql -u root
echo "还原完成。记得 docker compose start backend web"
