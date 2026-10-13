#!/usr/bin/env bash
# Clear conflicting local edits (already backed up), then ff-only pull.
set -euo pipefail
cd /home/eagle/dopams-cctns-ai

BACKUP_DIR=$(ls -1d logs/cutover_backup_* | tail -1)
echo "using backup $BACKUP_DIR"

# Preserve any server-only untracked diagnostics that are NOT in the incoming commit.
mkdir -p "$BACKUP_DIR/untracked_moved"
for f in \
  etl3/diagnostics/_ai_attempt_snapshot.py \
  etl3/diagnostics/_ai_backfill_preflight.py \
  etl3/diagnostics/_ensure_ai_env.py \
  etl3/diagnostics/_graceful_stop_ai_backfill.sh \
  etl3/diagnostics/_inspect_ai_backfill.py \
  etl3/diagnostics/_monitor_ai_backfill.py \
  etl3/diagnostics/_ollama_ping.py \
  etl3/diagnostics/_start_ai_backfill.sh
do
  if [ -e "$f" ]; then
    mv -f "$f" "$BACKUP_DIR/untracked_moved/"
    echo "moved $f"
  fi
done

# Discard local modifications to files that the pull will replace (backed up).
git checkout -- \
  etl3/config/.env.example \
  etl3/config/settings.py \
  etl3/enrichment/ai.py \
  etl3/enrichment/runner.py \
  etl3/tests/test_ollama_config.py

# run_ai_backfill.py may be clean or dirty — restore from HEAD then pull.
git checkout -- etl3/run_ai_backfill.py 2>/dev/null || true

git status -sb
git pull --ff-only origin cctns-ai
echo "HEAD=$(git rev-parse HEAD)"
git log -1 --oneline

# Verify reviewed markers
grep -q 'def run_per_fir_ai_backfill' etl3/enrichment/runner.py
grep -q 'def process_drug_for_crime' etl3/enrichment/runner.py
grep -q 'def process_accused_ai_for_crime' etl3/enrichment/runner.py
grep -q 'run_per_fir_ai_backfill' etl3/run_ai_backfill.py
grep -q 'accused_pending' etl3/run_ai_backfill.py
grep -q 'schedule=per-fir\|per-FIR\|per-fir' etl3/run_ai_backfill.py
test -f etl3/tests/test_per_fir_ai_scheduler.py

# Compare hashes to local reviewed commit objects
git show HEAD:etl3/enrichment/runner.py | sha256sum
git show HEAD:etl3/run_ai_backfill.py | sha256sum
sha256sum etl3/enrichment/runner.py etl3/run_ai_backfill.py

echo DEPLOY_OK
