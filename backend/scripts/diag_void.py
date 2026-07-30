"""Diagnose void score stagnation: why near-ATM zones don't register as voids."""
import urllib.request, json

# Get current market state with debug info
try:
    r = urllib.request.urlopen('http://localhost:8005/api/state', timeout=5)
    state = json.loads(r.read())
    spot = state.get("spot_price", 0)
    ai = state.get("advanced_intelligence", {})
    
    # Void debug
    p1 = ai.get("phase_1", {})
    void_data = p1.get("liquidity_voids", {})
    void_metrics = void_data.get("metrics", {})
    void_features = void_data.get("features", {})
    
    print(f"Spot: {spot}")
    print(f"Void score: {void_metrics.get('void_score', 'N/A')}")
    print(f"Void score raw: {void_metrics.get('void_score_raw', 'N/A')}")
    print(f"Void count: {void_metrics.get('total_void_count', 'N/A')}")
    print(f"True voids: {json.dumps(void_features.get('true_voids', [])[:5], indent=2)}")
    
    # Show void debug breakdown
    try:
        r2 = urllib.request.urlopen('http://localhost:8005/api/debug/liquidity-void', timeout=5)
        debug = json.loads(r2.read())
        print(f"\nVoid debug breakdown:")
        print(json.dumps(debug, indent=2))
    except Exception as e:
        print(f"No void debug endpoint: {e}")
        
except Exception as e:
    print(f"Backend not running or error: {e}")
    print("Will analyze from code instead")

# Direct analysis of LiquidityVoidEngine logic
print("\n" + "="*60)
print("STATIC ANALYSIS OF VOID DETECTION LOGIC")
print("="*60)
print("""
Current filter (line 138): match_score >= 2 out of 4:
  1. wide_spacing OR is_low_oi
  2. is_low_gamma (in gamma void zone)
  3. is_low_vol
  4. fast_delta (delta_change > 1.0)

For near-ATM strikes:
  - spacing_pct: typically 0.1-0.5% (NOT wide, < 1.5%)
  - OI: typically HIGH near ATM (NOT low)
  - gamma: typically HIGH near ATM (NOT in gamma void)
  - volume: typically HIGH near ATM (NOT low)
  - delta_change: call_delta + put_delta ≈ 0.5 + 0.5 = 1.0 (borderline)

Result: Near-ATM strikes score 0 or 1 out of 4 → NEVER become void candidates.
This is CORRECT behavior — near-ATM is NOT a liquidity void!

The problem is different: ALL void candidates are far OTM,
and with tiered proximity, far OTM (>50%) gets proximity=0.

REAL FIX: The void COMPOSITE score formula needs to weight 
OI/gamma/volume weakness MORE when proximity is low.
The current formula multiplies by match_quality which is proximity-independent.
""")
