"""
Shared source-adapter interface. The merger layer (not yet implemented --
later phases) talks to V1 and V2 only through this interface and never
touches a V1/V2-specific connection or table name directly.

Design note on run identity (important, found while building this): V1 and
V2 do NOT have symmetric "run" semantics.

  - V1's cctns_v1_etl_run_log.id is a monotonic BIGSERIAL -- "give me
    everything after id N" is a meaningful, efficient filter.
  - V2's etl_run_id is a per-cycle UUID with no inherent ordering across
    modules or time -- there is no "greater than" that means anything.
    Confirmed this session: the same etl_run_id is shared across multiple
    tables within one master_etl.py cycle, but different modules can have
    entirely different sets of run ids with no total order between them.

Because of this, every adapter method here takes `known_run_ids` (a set of
run ids the caller has ALREADY fully processed) rather than a single
"since" watermark. This is correct for both sources and does not pretend V2
has an ordering it doesn't have. The control-plane cursor (a later phase)
is what turns this into a persisted, incrementally-growing set.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class RunMetadata:
    source_system: str          # 'V1' | 'V2'
    source_module: str          # V1: 'fir'/'accused'/'accused_details'/'court' | V2: table name
    source_run_id: str          # V1: str(run_log.id) | V2: etl_run_id (uuid as str)
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    status: Optional[str]       # V1: run_log.status | V2: None (no run-level status exists)
    row_count: Optional[int]    # best-effort; None if not cheaply knowable


@dataclass(frozen=True)
class RecordRef:
    source_system: str
    source_module: str
    source_run_id: str
    source_record_id: str
    action: Optional[str] = None   # V1: row_action.action | V2: not tracked at this grain, None


@dataclass(frozen=True)
class GapEntry:
    source_system: str
    gap_type: str                 # 'ora_06502_window' | 'fk_retry_capped' | 'address_unresolved' | 'unlinked_accused'
    gap_key: str
    first_seen_at: Optional[datetime]
    status: str                   # 'OPEN' | 'RESOLVED'
    source_evidence_table: str
    detail: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    """Common read-only interface over a single source system."""

    source_system: str

    @abstractmethod
    def supported_modules(self) -> list:
        """Modules this adapter can discover runs/records for."""

    @abstractmethod
    def discover_new_runs(self, module: str, known_run_ids: Optional[set] = None) -> list:
        """RunMetadata list for `module`, excluding anything in known_run_ids.
        known_run_ids=None means "nothing known yet -- return everything"."""

    @abstractmethod
    def get_changed_records(self, module: str, run_id: str) -> list:
        """RecordRef list: which records did this specific run touch."""

    @abstractmethod
    def get_source_record(self, module: str, record_id: str) -> Optional[dict]:
        """Full current row for one record, as a plain dict. None if not found."""

    @abstractmethod
    def get_source_gap_state(self) -> list:
        """GapEntry list -- this source's own known, bounded incompleteness."""
