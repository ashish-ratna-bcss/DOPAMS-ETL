#!/usr/bin/env bash
# Backup production AI backfill files, pull cctns-ai, verify per-FIR markers.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai

echo '=== no competing writers ==='
pgrep -af 'run_ai_backfill.py|run_media_consolidation.py|run_phase4_consolidation.py|run_phase5_incremental.py|run_daily_pipeline.py' || echo none

TS=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR="logs/cutover_backup_${TS}"
mkdir -p "$BACKUP_DIR"
cp -a etl3/enrichment/runner.py "$BACKUP_DIR/runner.py"
cp -a etl3/run_ai_backfill.py "$BACKUP_DIR/run_ai_backfill.py"
# Also backup related AI files that commit updates
cp -a etl3/enrichment/ai.py "$BACKUP_DIR/ai.py" 2>/dev/null || true
cp -a etl3/config/settings.py "$BACKUP_DIR/settings.py" 2>/dev/null || true
echo "BACKUP_DIR=$BACKUP_DIR"
sha256sum etl3/enrichment/runner.py etl3/run_ai_backfill.py etl3/enrichment/ai.py etl3/config/settings.py | tee "$BACKUP_DIR/pre_pull.sha256"

echo '=== git status before pull ==='
git status -sb
git rev-parse HEAD
git branch --show-current

echo '=== pull origin/cctns-ai ==='
git fetch origin cctns-ai
git pull --ff-only origin cctns-ai
git rev-parse HEAD
git log -1 --oneline

echo '=== verify per-FIR markers ==='
grep -n 'run_per_fir_ai_backfill\|ai-per-fir\|schedule=per-fir\|accused_pending\|process_drug_for_crime' \
  etl3/enrichment/runner.py etl3/run_ai_backfill.py | head -40

test -f etl3/tests/test_per_fir_ai_scheduler.py
grep -q 'run_per_fir_ai_backfill' etl3/enrichment/runner.py
grep -q 'per-FIR' etl3/run_ai_backfill.py || grep -q 'per-fir' etl3/run_ai_backfill.py

sha256sum etl3/enrichment/runner.py etl3/run_ai_backfill.py etl3/enrichment/ai.py etl3/config/settings.py \
  | tee "$BACKUP_DIR/post_pull.sha256"

echo "DEPLOY_OK backup=$BACKUP_DIR head=$(git rev-parse --short HEAD)"
