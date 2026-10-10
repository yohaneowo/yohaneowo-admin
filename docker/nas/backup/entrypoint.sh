#!/usr/bin/env bash
# 备份服务：等 MySQL 就绪 → 确认只读账号 → 每天 BACKUP_TIME 执行一次 /backup.sh。
set -euo pipefail

BACKUP_TIME="${BACKUP_TIME:-04:00}"

echo "等待 MySQL 就绪…"
until MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysqladmin -h mysql -u root ping --silent 2>/dev/null; do
	sleep 5
done

# 只读账号给 DBeaver 之类的工具浏览数据用（只能 SELECT）。每次启动都按 .env 的密码重设，
# 所以改密码只要改 .env 再重启本服务；没设密码就不建立。
if [[ -n "${MYSQL_READONLY_PASSWORD:-}" ]]; then
	MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql -h mysql -u root <<SQL
CREATE USER IF NOT EXISTS 'readonly'@'%' IDENTIFIED BY '${MYSQL_READONLY_PASSWORD}';
ALTER USER 'readonly'@'%' IDENTIFIED BY '${MYSQL_READONLY_PASSWORD}';
GRANT SELECT, SHOW VIEW ON fastapiadmin.* TO 'readonly'@'%';
SQL
	echo "只读账号 readonly 已就绪"
fi

echo "每天 $BACKUP_TIME 自动备份，保留 ${BACKUP_KEEP_DAYS:-30} 天"
while true; do
	now=$(date +%s)
	next=$(date -d "today $BACKUP_TIME" +%s)
	if [[ "$next" -le "$now" ]]; then
		next=$(date -d "tomorrow $BACKUP_TIME" +%s)
	fi
	sleep $((next - now))
	/backup.sh || echo "自动备份失败，下次再试"
done
