import json
from pathlib import Path

p = Path("/home/eagle/dopams-cctns-ai/etl3/diagnostics/reports/media_gap_audit_20261009T074516Z.json")
d = json.loads(p.read_text())
print("V1_WRITE", d.get("V1_write"))
print("V2_WRITE", d.get("V2_write"))
vm = d.get("v2_media", {})
print("V2_DB", vm.get("database"))
print("V2_ERR", vm.get("error"))
print("V2_TABLE", vm.get("files_table"))
print("V2_CAND", vm.get("candidate_tables"))
print("HTTP400", vm.get("http_400"))
print("PENDING_CP", vm.get("pending_case_propertyish"))
print("BY_SOURCE_FIELD")
for row in vm.get("by_source_field") or []:
    print(row)
print("ERR_BUCKETS")
for row in (vm.get("error_buckets") or [])[:30]:
    print(row)
print("V1_ENTITY")
for row in d["v1_media"].get("by_entity_status", []):
    print(row)
print("DISK", d["v1_media"].get("disk_sample"))
print("ZERO", d["v1_media"].get("zero_byte_success_statuses"))
print("ERR_SAMPLES")
for row in (d["v1_media"].get("error_samples") or [])[:20]:
    print(row)
sa = d.get("station_ambiguous", {})
print("STATION_COUNT", sa.get("count"), "CLASS", sa.get("classification_now"))
for item in (sa.get("items") or [])[:5]:
    print("STATION_ITEM", {k: item.get(k) for k in list(item)[:8]})
print("COVERAGE", d.get("coverage"))
print("UNIFIED", d.get("etl3_gaps", {}).get("unified_counts"))
