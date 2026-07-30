import asyncio
from api.rest_client import BybitRestClient
import numpy as np
from scipy.interpolate import PchipInterpolator

async def main():
    client = BybitRestClient()
    tickers = await client.get_option_tickers()
    spot_data = await client.get_spot_ticker()
    spot = float(spot_data.get("lastPrice", 0))
    await client.close()

    # Filter for nearest expiry
    expiries = set()
    for t in tickers:
        parts = t["symbol"].split("-")
        if len(parts) >= 2:
            expiries.add(parts[1])
    
    # Sort expiries by date roughly
    # For now just pick one expiry
    expiries = sorted(list(expiries))
    target_expiry = expiries[0] if expiries else None
    
    strikes_data = {}
    for t in tickers:
        parts = t["symbol"].split("-")
        if len(parts) >= 4 and parts[1] == target_expiry:
            strike = float(parts[2])
            typ = parts[3]
            if strike not in strikes_data:
                strikes_data[strike] = {}
            try:
                delta = float(t.get("delta", 0))
                iv = float(t.get("markIv", 0))
                strikes_data[strike][typ] = {"delta": delta, "iv": iv}
            except:
                pass
                
    # Extract CDF
    k_list = []
    cdf_list = []
    
    for k in sorted(strikes_data.keys()):
        data = strikes_data[k]
        c_delta = data.get("C", {}).get("delta")
        p_delta = data.get("P", {}).get("delta")
        
        # CDF(K) approx = 1 - C_delta, or -P_delta
        cdf_vals = []
        if c_delta is not None and c_delta > 0 and c_delta < 1:
            cdf_vals.append(1 - c_delta)
        if p_delta is not None and p_delta < 0 and p_delta > -1:
            cdf_vals.append(-p_delta)
            
        if cdf_vals:
            k_list.append(k)
            cdf_list.append(np.mean(cdf_vals))

    if not k_list:
        print("No valid deltas found")
        return
        
    print(f"Got {len(k_list)} valid strikes for {target_expiry}")
    
    # Monotonic interpolation (PCHIP is monotonic)
    interp = PchipInterpolator(k_list, cdf_list)
    
    x = np.linspace(min(k_list), max(k_list), 100)
    pdf = interp.derivative()(x)
    
    print("X sample:", x[:5])
    print("PDF sample:", pdf[:5])
    
    # Check max probability
    max_idx = np.argmax(pdf)
    print(f"Max probability at strike: {x[max_idx]:.2f}, Spot is {spot}")
    print("It works!")

asyncio.run(main())
