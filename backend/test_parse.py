import sys, asyncio
sys.path.insert(0, '.')
from api.rest_client import BybitRestClient
from engine.data_manager import DataManager

async def test():
    c = BybitRestClient()
    t = await c.get_option_tickers()
    print(f"count: {len(t)}")
    if t:
        print(f"sample symbol: {t[0].get('symbol')}")
        print(f"ticker keys: {t[0]}")
        # Test _parse_tickers
        dm = DataManager()
        dm._parse_tickers(t)
        print(f"expiries: {dm.expiries[:5]}")
        print(f"chain keys: {list(dm.chain.keys())[:5]}")
        print(f"tickers_count: {len(dm.tickers)}")
    await c.close()

asyncio.run(test())
