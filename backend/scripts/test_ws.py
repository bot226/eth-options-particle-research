import asyncio
import websockets

async def test():
    try:
        ws = await websockets.connect("wss://stream.bybit.com/v5/public/option", open_timeout=10)
        print("Bybit option WS: OK")
        await ws.close()
    except Exception as e:
        print(f"Bybit option WS: FAILED - {e}")
    
    try:
        ws = await websockets.connect("wss://stream.bybit.com/v5/public/linear", open_timeout=10)
        print("Bybit linear WS: OK")
        await ws.close()
    except Exception as e:
        print(f"Bybit linear WS: FAILED - {e}")
    
    try:
        ws = await websockets.connect("wss://www.deribit.com/ws/api/v2", open_timeout=10)
        print("Deribit WS: OK")
        await ws.close()
    except Exception as e:
        print(f"Deribit WS: FAILED - {e}")

asyncio.run(test())
