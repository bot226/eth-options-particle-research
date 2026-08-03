"""MOS Version Contract — Single source of truth for all runtime versioning.

All engines and loggers MUST import version constants from here.
Never define CODE_VERSION, RESEARCH_SCHEMA_VERSION, or ENGINE_PATCH_VERSION elsewhere.
"""

CODE_VERSION = "research_fix_2026_08_03_v58"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v65_deribit_rest_circuit_breaker"
DATASET_EXPORTER_VERSION = "1.1.0"
PARTICLE_LOGIC_VERSION = "particle_shadow_v3"

# Synthetic Flow Pressure Scale Contract
# -100 to +100, 0 = neutral
# negative = sell pressure, positive = buy pressure
# abs(value) = intensity
FLOW_PRESSURE_SCALE = "-100_to_100_neutral_0"
