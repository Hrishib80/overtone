from typing import Dict
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends
from backend.auth import verify_ws_token
from backend.database import AsyncSessionLocal, ChatMessage, ChatStatus
from backend.config import ALLOWED_ORIGINS
import json

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Dict[str, WebSocket]] = {}
        self.user_rooms: Dict[str, str] = {}

    async def connect(self, ws: WebSocket, room_id: str, user_id: str):
        if room_id not in self.active_connections:
            self.active_connections[room_id] = {}
        self.active_connections[room_id][user_id] = ws
        self.user_rooms[user_id] = room_id

    def disconnect(self, room_id: str, user_id: str):
        if room_id in self.active_connections:
            if user_id in self.active_connections[room_id]:
                del self.active_connections[room_id][user_id]
            if not self.active_connections[room_id]:
                del self.active_connections[room_id]
        if user_id in self.user_rooms:
            del self.user_rooms[user_id]

    async def send_to_user(self, room_id: str, user_id: str, message: dict):
        if room_id in self.active_connections and user_id in self.active_connections[room_id]:
            try:
                await self.active_connections[room_id][user_id].send_json(message)
            except RuntimeError:
                pass # Ignore if disconnected

    async def broadcast_to_room(self, room_id: str, message: dict, exclude_user: str = None):
        if room_id in self.active_connections:
            for uid, ws in list(self.active_connections[room_id].items()):
                if uid != exclude_user:
                    try:
                        await ws.send_json(message)
                    except RuntimeError:
                        pass # Ignore disconnected websocket

manager = ConnectionManager()

@router.websocket("/ws/signal/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: str, token: str = Query(...)):
    user_id = verify_ws_token(token)
    if not user_id:
        await websocket.close(code=1008)
        return
        
    origin = websocket.headers.get("origin")
    if ALLOWED_ORIGINS != ["*"] and origin not in ALLOWED_ORIGINS:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    await manager.connect(websocket, room_id, user_id)
    await manager.broadcast_to_room(room_id, {"type": "peer-joined", "user_id": user_id}, exclude_user=user_id)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type")
            if msg_type == "ping":
                await websocket.send_json({"type": "pong"})
                continue
            
            if msg_type == "chat-message":
                async with AsyncSessionLocal() as db:
                    message_data = msg.get("message", {})
                    new_msg = ChatMessage(
                        match_id=room_id,
                        from_user_id=user_id,
                        message_text=message_data.get("text")
                    )
                    db.add(new_msg)
                    await db.commit()
                await manager.broadcast_to_room(room_id, msg, exclude_user=user_id)
            elif msg_type in ["typing", "read-receipt"]:
                if msg_type == "read-receipt":
                    # Future: Update status in db to read
                    pass
                await manager.broadcast_to_room(room_id, msg, exclude_user=user_id)
            elif msg_type in ["key-exchange", "offer", "answer", "sdp-offer", "sdp-answer", "ice-candidate",
                              "call-request", "call-accept", "call-reject", "call-end",
                              "video-request", "video-accept", "video-reject"]:
                target_user = msg.get("target_user_id")
                msg["from_user_id"] = user_id
                if target_user:
                    await manager.send_to_user(room_id, target_user, msg)
                else:
                    await manager.broadcast_to_room(room_id, msg, exclude_user=user_id)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        await websocket.close(code=1011)
    finally:
        manager.disconnect(room_id, user_id)
        await manager.broadcast_to_room(room_id, {"type": "peer-left", "user_id": user_id}, exclude_user=user_id)
