"""
V1's cctns_accused dossier has no stable per-logical-entity identifier --
accused_id (and natural_key) churn on any field edit (confirmed Phase 2/3:
one person_code had 69 distinct accused_id values over time). Current-state
computation for V1 accused must therefore group accused_source observations
by a LOGICAL key built from the natural-key input fields themselves, not by
accused_id, per ETL3_MERGER_IMPLEMENTATION_PLAN.md section 10.

Logical key: fir_reg_num + normalized(accused_name) + normalized(father_name)
+ (normalized(mobile_1) or normalized(dob), whichever populated). This is
the same field set the original natural-key design was built from, before
MD5 churn makes accused_id unusable as a grouping key.
"""
import re


def _norm(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip().lower())


def logical_key(payload: dict) -> str:
    fir = _norm(payload.get("fir_reg_num"))
    name = _norm(payload.get("accused_name"))
    father = _norm(payload.get("father_name"))
    mobile = _norm(payload.get("mobile_1"))
    dob = _norm(payload.get("dob"))
    stable_extra = mobile or dob
    return f"{fir}|{name}|{father}|{stable_extra}"
