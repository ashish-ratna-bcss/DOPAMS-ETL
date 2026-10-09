import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from etl3.sync.reconcile import classify_gap

assert classify_gap("ambiguous_v1_station_name") == "UNRESOLVED_RELATIONSHIP"
assert classify_gap("ambiguous_v1_ps_code") == "UNRESOLVED_RELATIONSHIP"
print("CLASSIFIER_OK")
