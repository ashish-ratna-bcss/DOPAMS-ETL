# Address Resolution Source Investigation — Maharashtra vs. Jammu & Kashmir/Srinagar

Investigates the one record flagged in
`docs/ETL3_DOMICILE_RECONCILIATION_REPORT.md` ("other changes") whose
address resolution changed from a bare `Maharashtra` (no district) to
`Jammu and Kashmir`/`Srinagar`. All investigation was read-only. No
production table was written, no migration applied, no feature flag
changed, no service restarted.

Personal identifiers (`person_id`, raw `accused_id`, the accused's name
and father's name) are redacted in this report. They are retained only in
this session's terminal history on the operational server, not in any
committed file.

## 1. Source system and endpoint

This project's ETL3 has **no HTTP REST client for V1**. Confirmed by
inspecting `etl3/db/connections.py` and `etl3/sources/v1/adapter.py`: the
authoritative V1 source, as this codebase itself defines and uses it, is
a **direct, read-only Postgres connection to the `cctns_v1` database**
(`connections.get_v1_source_connection()` — forces
`default_transaction_read_only=on` at the session level and asserts
`current_database()` before any query runs). The raw table is
`cctns.cctns_accused_details`, confirmed via
`etl3/sources/v1/adapter.py`'s `MODULE_TABLE["accused_details"] =
("cctns_accused_details", "accused_id")`.

This connection and query pattern is this investigation's "source API" —
there is no other source to fetch from for a V1 record, and substituting
anything else would not be source evidence.

## 2. Fetch outcome

- Query: `SELECT ... FROM cctns.cctns_accused_details WHERE fir_reg_num =
  '2023055180049'` (the FIR linked to the flagged person, found via
  `accused_unified` → `crimes_unified` join), read-only, no network/API
  credentials beyond the existing `.env`-configured database role.
- Fetched: 2026-10-24T00:31Z.
- Response: **success**, 3 rows (the 3 accused on this one FIR).
- The flagged record is `accused_id = 6654`, `person_code =
  '202305518004934002'` — confirmed to be the exact same person as
  `persons_unified.person_id = '202305518004934002'` / `source_record_id
  = '6654'` already identified in the domicile report.

## 3. Redacted original source fields (raw V1, `cctns.cctns_accused_details`)

| Field | Value |
|---|---|
| `accused_name` | *(redacted — personal identifier)* |
| `father_name` | *(redacted — personal identifier)* |
| `gender` | Male |
| `age` | 27 |
| `nationality` | `India` |
| `accused_present_address` | `Near Sonic Guest,house, Ajuna Beach,Goa, MH,Srinagar,Jammu & Kashmir` |
| `accused_permanent_address` | `Near Sonic Guest,house, Ajuna Beach,Goa, MH,Srinagar,Jammu & Kashmir` (identical string, both fields) |
| `created_at` | 2026-09-28 12:19:36 |
| `updated_at` | 2026-09-28 12:19:36 (**identical to `created_at`** — this record has never been revised at the source since it was first entered) |

**Fields confirmed absent from the V1 source for this table:** there is
no structured `state`, `district`, `mandal`, `village`, `pincode`, or any
other coded geography column anywhere in `cctns_accused_details` (full
column list: `accused_id, fir_reg_num, person_code, fir_no, reg_year,
reg_dt, unit, ps_name, section_of_law, fir_status, accused_name,
father_name, gender, age, caste, nationality, occupation, mobile_1,
telephone_residence, is_arrested, arrest_surrender_dt,
accused_present_address, accused_permanent_address, created_at,
updated_at, natural_key`). For V1, the two free-text address fields are
the **entirety** of the geographic evidence available anywhere upstream
of ETL3 — there is nothing else to check.

## 4. API (source) vs. database (ingested) comparison

| Field | Original V1 source value | Stored in `persons_unified` | Match |
|---|---|---|---|
| Present address (free text) | `Near Sonic Guest,house, Ajuna Beach,Goa, MH,Srinagar,Jammu & Kashmir` | identical | ✅ |
| Permanent address (free text) | `Near Sonic Guest,house, Ajuna Beach,Goa, MH,Srinagar,Jammu & Kashmir` | identical | ✅ |
| Nationality | `India` | `India` (raw `nationality` column; enrichment's resolved value is separate) | ✅ |
| Present/permanent state, district, mandal, village, country (structured) | **not present at the source at all** | not present (V1 has no such columns) | N/A — nothing to compare |

**Conclusion for this phase:** the ETL ingestion / field-mapping step
(`etl3/merger/field_maps.py`'s `PERSONS["V1"]["map"]`, which maps
`present_address_text` ← `accused_present_address` and
`permanent_address_text` ← `accused_permanent_address` verbatim) is
**byte-for-byte faithful**. The free-text address was not altered, split,
or corrupted during ingestion. The ambiguity originates entirely in the
source text itself, not in ETL3's reading of it.

## 5. Step-by-step ETL3 transformation trace

Replayed directly against the real, deployed `resolve_address_text()`
with the real knowledge base loaded from `dopams_cctns_v2`, using the
exact source text above as input.

1. **Input:** `"Near Sonic Guest,house, Ajuna Beach,Goa, MH,Srinagar,Jammu & Kashmir"`
2. **Tokenization** (`resolve_address_text` splits on `,`/`/`, then
   `norm_token()`s each piece): `["Near Sonic Guest", "house", "Ajuna
   Beach", "Goa", "MH", "Srinagar", "Jammu & Kashmir"]`
3. **State search** (iterates tokens from the **last** backward to the
   first, calling `expand_state()` then `kb.canon_state()`, then
   `_fuzzy_state()` as a fallback):
   - Token `"Jammu & Kashmir"` (last, checked first): `expand_state()` →
     looks up `STATE_ALIASES["jammu & kashmir"]` → `"Jammu and Kashmir"`
     → `kb.canon_state("Jammu and Kashmir")` → **`"Jammu And Kashmir"`
     (exact KB match, confirmed live)**. State found immediately;
     `state_idx` = index of this token. Loop stops here.
4. **District search** (iterates every other token, calling
   `_match_district_token(kb, "Jammu And Kashmir", token)`):
   - `"Srinagar"` → `expand_district()` (no alias) → title-cased
     `"Srinagar"` → `kb.canon_district("Jammu And Kashmir",
     "Srinagar")` → **`"Srinagar"` (exact KB match, confirmed live —
     Srinagar is a real district of Jammu and Kashmir in
     `kb.geo_reference`)**.
   - All other tokens (`"Goa"`, `"MH"`, `"Ajuna Beach"`, etc.) checked
     and do not match any Jammu and Kashmir district — correctly
     ignored once a hit is found.
5. **Country:** `kb.country_of_indian_state("Jammu And Kashmir")` →
   `"India"` (Jammu and Kashmir is in the KB's Indian-state set).
6. **Output:** `{"country": "India", "state": "Jammu And Kashmir",
   "district": "Srinagar", "mandal": None, "path": "kb+text"}`
7. **Rule check:** follows the documented rules exactly — searches from
   the most-specific (last) token first, finds a complete state+district
   pair within one consistent state, never mixes tokens across states,
   never guesses a state the text/KB doesn't support.

**Determinism:** re-run 3 times independently against a freshly loaded
KB; identical output every time. This is not an instance of the known,
separately-tracked tied-fuzzy-match non-determinism bug — the state
match here is an **exact** KB lookup (via the alias), not a fuzzy
tie-break.

### Why the previously stored result was `Maharashtra`, no district

Replayed the same text through what the pre-`1ef60f8` code (no
`STATE_ALIASES` dict existed yet) would have done:

- `"Jammu & Kashmir"`: `expand_state()` had no alias lookup available,
  so it fell back to title-casing the literal text →
  `"Jammu & Kashmir"` (ampersand kept). `kb.canon_state("Jammu &
  Kashmir")` → **`None`** (the KB's canonical name uses "and", not "&" —
  confirmed live). Fuzzy fallback: `trigram_similarity("Jammu &
  Kashmir", "Jammu and Kashmir")` = **0.619**, below the `SIM_STATE =
  0.80` floor — confirmed live, so the fuzzy path also would not have
  caught it. State search continues to the previous token.
- `"Srinagar"`: not a state, no match.
- `"MH"`: a 2-letter code in `STATE_ABBREV` (pre-existing, unrelated to
  this phase's changes) → `"Maharashtra"` → `kb.canon_state("Maharashtra")`
  → match. State found here.
- District search within `"Maharashtra"`: `kb.canon_district("Maharashtra",
  "Srinagar")` → **`None`** (confirmed live — Srinagar is not a
  Maharashtra district) — and no other token matches a Maharashtra
  district either. District stays `None`.

This fully and mechanistically explains both the old and the new result
from the same unchanged parsing algorithm: the only thing that changed
is that `STATE_ALIASES` (added in this phase's commit `1ef60f8`, for an
unrelated purpose — canonicalizing historical CCTNS state spellings such
as `Orissa`, `Pondicherry`, `Uttaranchal`, and `Jammu & Kashmir`) made a
previously-unreachable, but textually present and KB-valid, state+district
pair reachable. Confirmed via `git diff e3c9ff0 HEAD --
etl3/enrichment/address.py` that `resolve_address_text`, `_candidate`,
`resolve_candidate`, and `_merge_address_hits` — the functions that
*execute* the lookup — are themselves byte-for-byte unchanged; only the
alias *data* (`STATE_ALIASES`) is new.

## 6. Knowledge-base evidence

Confirmed live against the actual `dopams_cctns_v2.kb.geo_reference`/
`kb.geo_countries` tables (not assumed):

- `kb.canon_state("Jammu and Kashmir")` → `"Jammu And Kashmir"`: Jammu
  and Kashmir is a real, present state/UT in the KB.
- `kb.canon_district("Jammu and Kashmir", "Srinagar")` → `"Srinagar"`:
  Srinagar is a real, present district of Jammu and Kashmir in the KB,
  under that exact state.
- `kb.canon_district("Maharashtra", "Srinagar")` → `None`: Srinagar is
  **not** a district of Maharashtra under any name in the KB — the old
  stored result's own district search necessarily failed for exactly
  this reason, which is corroborating evidence, not a coincidence.

Srinagar being a well-known city does not by itself establish the
state — the conclusion here rests on the KB's own district-to-state
mapping, confirmed live, not on general knowledge of Indian geography.

## 7. Stored vs. recomputed comparison

| | Stored (pre-`1ef60f8`) | Recomputed (post-`1ef60f8`, current) |
|---|---|---|
| State | `Maharashtra` | `Jammu And Kashmir` |
| District | `None` | `Srinagar` |
| Path | `kb+text` | `kb+text` |
| Matching mechanism | 2-letter abbreviation (`MH`) in `STATE_ABBREV`, pre-existing | Full-phrase alias (`STATE_ALIASES["jammu & kashmir"]`), added this phase |
| Internally consistent? | **No** — district search for the matched state found nothing | **Yes** — state and district are a real, KB-confirmed pair |

## 8. Root-cause conclusion

**`SOURCE API CONFIRMS CURRENT RESOLUTION`** — with a stated residual
ambiguity, not absolute certainty.

The raw V1 source record (fetched directly, confirmed byte-identical to
what is stored in `persons_unified` — ruling out ETL ingestion or field
mapping as a cause) contains one genuinely ambiguous free-text address
string that names multiple real places (`Ajuna Beach`/`Goa`, `MH`,
`Srinagar`/`Jammu & Kashmir`) with no structured field anywhere upstream
to disambiguate them. Of the two candidate resolutions the parsing
algorithm can produce from this one string:

- The **old** result (`Maharashtra`, no district) paired a state
  abbreviation with a district that does not belong to it — a provably
  incomplete, internally inconsistent match.
- The **new** result (`Jammu and Kashmir`, `Srinagar`) is a provably
  complete, internally consistent, KB-confirmed real state+district
  pair, found by the same unchanged algorithm once a documented,
  separately-justified alias made the full phrase `"Jammu & Kashmir"`
  reachable.

Given both candidates come from the same unmodified algorithm applied
to confirmed-faithful source text, and the new candidate is the more
complete and internally consistent of the two, the current (recomputed)
resolution is the better-supported one. The residual ambiguity — `Goa`/
`Ajuna Beach`/`MH` are also real, valid place references in the same
string, most plausibly describing where this person was staying/working
(a well-documented real-world pattern of vendors from Kashmir operating
near Goa's beaches) rather than their home state — cannot be fully
resolved from this source field alone, since V1 gives ETL3 no way to
tell "this fragment is where they're reachable" from "this fragment is
where they're from" within one free-text blob duplicated identically
into both the present and permanent address columns.

**Confidence: moderate-to-high that the current resolution is better
than the old one; not absolute, because the source data itself is
genuinely ambiguous, not because of any further code concern.**

## 9. Regression test

**Not added.** This investigation did not confirm a logic bug — the
current behavior is the intended, better-reasoned result of the
`STATE_ALIASES` feature working as designed, not an accidental
side effect. Per the investigation's own scope ("create a minimal
regression test... if a logic bug is confirmed"), no fix is proposed and
no test was required. If a confirmatory regression test capturing this
exact case (full-phrase state alias beating an abbreviation whose
district search fails) would still be useful to protect against a future
regression, that is a separate, optional addition — not performed here
without being asked, since it is not fixing anything.

Two sibling accused on the same FIR were re-verified against the current
code as a nearby-case sanity check and both still resolve correctly
(a `Nigeria` nationality → `international`; a `Hyderabad`/`Telangana`
address → `native state`, fully resolved) — nothing nearby is broken.

## 10. Impact assessment

No wider audit is needed beyond what
`docs/ETL3_DOMICILE_RECONCILIATION_REPORT.md` already captured: of its
6 "other" (address-resolution-differing) rows, this is the **only** one
matching this specific "complete pair replaces an incomplete pair"
pattern; the other 5 are a different, unambiguously benign pattern
(a previously-unresolved address slot now resolving via free text, not a
value changing from one resolved state to a different one). This
confirms the pattern is narrow (1 of 53,182 rows), not a class-wide issue
requiring a separate query sweep.

## Recommendation

**Retain the current (recomputed) resolution** — `Jammu and
Kashmir`/`Srinagar` — as the better-supported interpretation of a
genuinely ambiguous but faithfully-ingested source address. Do not
revert to the old `Maharashtra`/no-district value, which is provably
incomplete for the same source text. No code or migration change is
needed; this row would already be corrected by applying the
domicile/nationality backfill already presented in
`ETL3_DOMICILE_RECONCILIATION_REPORT.md` §"other changes," *if* that
bucket is ever expanded to include address-resolution-only differences
— which it currently, deliberately, is not. No production data was
changed in this investigation.

**2026-10-24 update:** the 27-row domicile/nationality backfill referenced
above has since been executed in production. This record was, as stated,
correctly **excluded** from that backfill — confirmed in the writer's own
output (its `nationality`/`domicile_classification`/`domicile_method`
were not part of the 27 rows written; it remains one of the 6 "other"
rows, address-resolution-only, left exactly as this investigation found
it). This investigation's conclusion and recommendation are unaffected
and unchanged.
