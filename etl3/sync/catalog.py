"""Module registry shared by cursor advancement, catch-up, and reconciliation.

The observation table is the exclusion set. consolidation_cursor stores one
high-water run id for monitoring. It is not used to decide which source
rows are still unseen.
"""

SENTINELS = {"__initial__", "__initial_no_run_id__"}

# collapse: unified row count is intentionally below the source row count
# (V1 accused logical grouping). derived: the unified table is not 1:1 with
# this module's primary key (V1 seizures are projected from the dossier).
MODULES = [
    {"source_system": "V1", "module": "fir", "obs_table": "crimes_source", "source_table": "fir",
     "unified_table": "crimes_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V1", "module": "accused", "obs_table": "accused_source", "source_table": "accused",
     "unified_table": "accused_unified", "has_source_system": True, "collapse": True},
    {"source_system": "V1", "module": "accused_details", "obs_table": "arrests_source", "source_table": "accused_details",
     "unified_table": "arrests_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V1", "module": "court", "obs_table": "chargesheets_source", "source_table": "court",
     "unified_table": "chargesheets_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "crimes", "obs_table": "crimes_source", "source_table": "crimes",
     "unified_table": "crimes_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "accused", "obs_table": "accused_source", "source_table": "accused",
     "unified_table": "accused_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "persons", "obs_table": "persons_source", "source_table": "persons",
     "unified_table": "persons_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "arrests", "obs_table": "arrests_source", "source_table": "arrests",
     "unified_table": "arrests_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "chargesheets", "obs_table": "chargesheets_source", "source_table": "chargesheets",
     "unified_table": "chargesheets_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "charge_sheet_updates", "obs_table": "chargesheets_source",
     "source_table": "charge_sheet_updates", "unified_table": "chargesheets_unified",
     "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "mo_seizures", "obs_table": "seizures_source", "source_table": "mo_seizures",
     "unified_table": "seizures_unified", "has_source_system": True, "collapse": False},
    {"source_system": "V2", "module": "properties", "obs_table": "properties_source", "source_table": "properties",
     "unified_table": "properties_unified", "has_source_system": False, "collapse": False},
    # Observed for evidence, not merged. fsl_unified is not a business table
    # this consolidation maintains; a shortfall there is not missing data.
    {"source_system": "V2", "module": "fsl_case_property", "obs_table": "fsl_source", "source_table": "fsl_case_property",
     "unified_table": "fsl_unified", "has_source_system": False, "collapse": False,
     "excluded_from_unified": True},
    {"source_system": "V2", "module": "disposal", "obs_table": "disposal_source", "source_table": "disposal",
     "unified_table": "disposal_unified", "has_source_system": False, "collapse": False},
    {"source_system": "V2", "module": "interrogation_reports", "obs_table": "interrogation_source",
     "source_table": "interrogation_reports", "unified_table": "interrogation_unified",
     "has_source_system": False, "collapse": False},
    {"source_system": "V2", "module": "hierarchy", "obs_table": "hierarchy_source", "source_table": "hierarchy",
     "unified_table": "hierarchy_unified", "has_source_system": False, "collapse": False},
]


def adapter_for(source_system: str):
    if source_system == "V1":
        from etl3.sources.v1.adapter import V1Adapter
        return V1Adapter()
    if source_system == "V2":
        from etl3.sources.v2.adapter import V2Adapter
        return V2Adapter()
    raise ValueError(source_system)
