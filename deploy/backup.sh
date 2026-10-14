#!/usr/bin/env bash
# 语料 + 数据库定时备份（放 crontab：0 3 * * * /app/deploy/backup.sh）
set -e
TS=$(date +%Y%m%d_%H%M%S)
OUT=${BACKUP_DIR:-/app/backups}
mkdir -p "$OUT"
tar -czf "$OUT/knowledge_$TS.tar.gz" -C /app knowledge 2>/dev/null || true
if [ -n "$DATABASE_URL" ]; then
  pg_dump "$DATABASE_URL" | gzip > "$OUT/db_$TS.sql.gz"
else
  cp /app/data/app.db "$OUT/db_$TS.db"
fi
ls -1t "$OUT" | tail -n +15 | while read -r f; do rm -f "$OUT/$f"; done   # 保留最近 7 天*2
echo "backup done -> $OUT ($TS)"
