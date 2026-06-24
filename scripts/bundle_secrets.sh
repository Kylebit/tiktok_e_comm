#!/usr/bin/env bash
# 在旧机器打包凭据与数据库，供新机器解压（勿上传 GitHub）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/../deploy-secrets-$(date +%Y%m%d).tar.gz}"
OZON="${ROOT}/../ozon/webapp"

echo "打包到: $OUT"
tar -czf "$OUT" \
  -C "$ROOT" \
  config/settings.json \
  tiktok_tokens.json \
  tiktok_ads_tokens.json \
  shopee_tokens.json \
  data/shop.db \
  2>/dev/null || true

if [[ -d "$OZON/data" ]]; then
  tar -czf "${OUT%.tar.gz}-ozon-data.tar.gz" -C "$OZON" data/
  echo "Ozon data: ${OUT%.tar.gz}-ozon-data.tar.gz"
fi

echo "完成: $OUT"
echo "请通过 U 盘/scp 传到新机器，不要 push 到 GitHub。"
