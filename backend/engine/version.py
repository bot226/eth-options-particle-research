"""MOS Version Contract — Single source of truth for all runtime versioning.

All engines and loggers MUST import version constants from here.
Never define CODE_VERSION, RESEARCH_SCHEMA_VERSION, or ENGINE_PATCH_VERSION elsewhere.
"""

CODE_VERSION = "eth_fork_2026_08_14_v1_from_btc_v70"
RESEARCH_SCHEMA_VERSION = "2.0"
ENGINE_PATCH_VERSION = "v92.4_eth_interval_parent_closure"
DATASET_EXPORTER_VERSION = "1.2.2"
INTERVAL_EXPORTER_VERSION = "92.4-eth.1"
PARTICLE_LOGIC_VERSION = "particle_shadow_v3"

# Synthetic Flow Pressure Scale Contract
# -100 to +100, 0 = neutral
# negative = sell pressure, positive = buy pressure
# abs(value) = intensity
FLOW_PRESSURE_SCALE = "-100_to_100_neutral_0"
