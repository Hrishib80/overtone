from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, or_, and_, func
from sqlalchemy.orm import aliased

from backend.database import get_db, ChatMessage, ChatStatus, MatchRecord, User
from backend.auth import require_auth

router = APIRouter(prefix="/api/chat", tags=["chat"])

@router.get("/{match_id}/messages")
async def get_messages(match_id: str, page: int = 1, limit: int = 50, user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    match_res = await db.execute(select(MatchRecord).where(MatchRecord.id == match_id))
    match = match_res.scalars().first()
    if not match or (match.user_a_id != user_id and match.user_b_id != user_id):
        raise HTTPException(status_code=403, detail="Not authorized")
        
    offset = (page - 1) * limit
    messages_query = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.match_id == match_id)
        .order_by(ChatMessage.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    db_messages = messages_query.scalars().all()
    
    # Frontend expects chronological order
    db_messages.reverse()
    
    formatted_messages = []
    for m in db_messages:
        formatted_messages.append({
            "id": m.id,
            "text": m.message_text or "",
            "isMine": m.from_user_id == user_id,
            "timestamp": m.created_at.timestamp() * 1000 if m.created_at else 0,
            "status": m.status.value if m.status else "sent"
        })
        
    peer_id = match.user_a_id if match.user_b_id == user_id else match.user_b_id
    peer_res = await db.execute(select(User).where(User.id == peer_id))
    peer = peer_res.scalars().first()
    
    peer_data = {
        "id": peer.id if peer else peer_id,
        "avatarUrl": peer.avatar_url if peer else "",
        "displayName": peer.display_name if peer else "Unknown",
        "isOnline": False
    }
    
    return {"messages": formatted_messages, "peer": peer_data}

@router.post("/{match_id}/read")
async def mark_read(match_id: str, user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    await db.execute(
        update(ChatMessage)
        .where(and_(ChatMessage.match_id == match_id, ChatMessage.from_user_id != user_id))
        .values(status=ChatStatus.read)
    )
    await db.commit()
    return {"success": True}

@router.get("/conversations")
async def get_conversations(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    # Fetch all matches for user in one query
    result = await db.execute(
        select(MatchRecord)
        .where(or_(MatchRecord.user_a_id == user_id, MatchRecord.user_b_id == user_id))
    )
    matches = result.scalars().all()
    
    if not matches:
        return {"conversations": []}
    
    # Collect all peer IDs and match IDs
    peer_ids = set()
    match_ids = set()
    match_to_peer = {}
    for match in matches:
        peer_id = match.user_a_id if match.user_b_id == user_id else match.user_b_id
        peer_ids.add(peer_id)
        match_ids.add(match.id)
        match_to_peer[match.id] = peer_id
    
    # Batch fetch all peers in one query
    peers_res = await db.execute(select(User).where(User.id.in_(peer_ids)))
    peers = {u.id: u for u in peers_res.scalars().all()}
    
    # Batch fetch last message per match using a subquery
    # Get all messages for these matches ordered by created_at desc
    all_msgs_res = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.match_id.in_(match_ids))
        .order_by(ChatMessage.match_id, ChatMessage.created_at.desc())
    )
    all_msgs = all_msgs_res.scalars().all()
    
    # Extract last message per match_id
    last_msg_by_match = {}
    for msg in all_msgs:
        if msg.match_id not in last_msg_by_match:
            last_msg_by_match[msg.match_id] = msg
    
    # Batch count unread messages per match
    unread_res = await db.execute(
        select(ChatMessage.match_id, func.count(ChatMessage.id))
        .where(and_(
            ChatMessage.match_id.in_(match_ids),
            ChatMessage.from_user_id != user_id,
            ChatMessage.status != ChatStatus.read
        ))
        .group_by(ChatMessage.match_id)
    )
    unread_counts = dict(unread_res.all())
    
    conversations = []
    for match in matches:
        peer_id = match_to_peer[match.id]
        peer = peers.get(peer_id)
        last_msg = last_msg_by_match.get(match.id)
        unread_count = unread_counts.get(match.id, 0)
        
        conversations.append({
            "matchId": match.id,
            "unreadCount": unread_count,
            "avatarUrl": peer.avatar_url if peer else "",
            "displayName": peer.display_name if peer else "Unknown",
            "lastMessageTime": last_msg.created_at.timestamp() * 1000 if last_msg and last_msg.created_at else match.created_at.timestamp() * 1000,
            "lastMessagePreview": last_msg.message_text if last_msg else "Matched!"
        })
        
    conversations.sort(key=lambda x: x["lastMessageTime"], reverse=True)
        
    return {"conversations": conversations}
