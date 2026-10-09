import json
from pathlib import Path

d = json.loads(Path("/home/eagle/dopams-cctns-ai/etl3/diagnostics/reports/media_schema_probe.json").read_text())
print("V1", json.dumps(d.get("v1_path_samples"), indent=2, default=str))
print("V2 DL", json.dumps(d.get("v2_downloaded_samples"), indent=2, default=str))
print("V2 NULL", json.dumps(d.get("v2_null_file_id_samples"), indent=2, default=str))
print("V2 cols", [c["name"] for c in d["v2_media_cols"]])
