"""WebSocket маршрут — real-time обновления для фронтенда."""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState
import logging

log = logging.getLogger(__name__)
router = APIRouter()

_dm = None


def set_data_manager(dm):
    global _dm
    _dm = dm


@router.websocket("/ws/stream")
async def ws_stream(websocket: WebSocket):
    """WebSocket для real-time push обновлений фронтенду.

    Протокол:
    - Клиент отправляет 'ping' каждые ~20s → сервер отвечает 'pong'
    - Сервер пушит {type: 'update'} при новых данных
    """
    await websocket.accept()
    log.info("Frontend WS client connected: %s", websocket.client)
    if _dm:
        _dm.add_ws_client(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # Respond to heartbeat ping
            if data == "ping":
                if websocket.client_state == WebSocketState.CONNECTED:
                    try:
                        await websocket.send_text("pong")
                    except Exception:
                        break  # socket dead
            # Ignore any other client messages
    except WebSocketDisconnect:
        log.info("Frontend WS client disconnected: %s", websocket.client)
    except Exception as e:
        log.warning("WS error (%s): %s", websocket.client, e)
    finally:
        if _dm:
            _dm.remove_ws_client(websocket)
