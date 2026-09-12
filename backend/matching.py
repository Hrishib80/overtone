import os
import asyncio
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, UploadFile, File
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from pinecone import Pinecone

from backend.config import PINECONE_API_KEY, PINECONE_NAMESPACE, INDEX_HOST, MEDIA_ROOT
from backend.database import get_db, AsyncSessionLocal, User, Prompt, Photo, Like, MatchRecord, ProcessingStatus, MatchStatus
from backend.auth import require_auth
from backend.moderation import moderate_media
from backend.embedding import build_vibe_vector
from backend.storage import upload_file

router = APIRouter(prefix="/api", tags=["matching"])

pc = None

def get_index():
    global pc
    if not PINECONE_API_KEY or not INDEX_HOST:
        raise RuntimeError("Matching service is not configured.")
    if pc is None:
        pc = Pinecone(api_key=PINECONE_API_KEY)
    return pc.Index(host=INDEX_HOST)

class MediaItem(BaseModel):
    asset_type: str
    local_file_path: str
    public_url: Optional[str] = None

class PromptInput(BaseModel):
    question: str
    answer: str

class DeepProfileInput(BaseModel):
    user_id: str
    display_name: str
    prompts: List[PromptInput]
    media: List[MediaItem]

class MatchRequest(BaseModel):
    user_id: str
    limit: int = 15

class LikeRequest(BaseModel):
    to_user_id: str
    target_type: str
    target_id: str
    comment_text: Optional[str] = None

def resolve_safe_media_path(relative_path: str) -> str:
    base = os.path.abspath(MEDIA_ROOT)
    target = os.path.abspath(os.path.join(base, relative_path))
    if not target.startswith(base):
        raise ValueError("Path traversal detected")
    return target

async def process_and_upsert_profile(profile: DeepProfileInput, resolved_paths: List[str]):
    try:
        from pathlib import Path
        paths = [Path(p) for p in resolved_paths]
        
        mod_ok = await moderate_media(paths)
        if not mod_ok:
            raise Exception("Moderation failed")
            
        prompt_texts = [f"Q: {p.question}\nA: {p.answer}" for p in profile.prompts]
        vector, transcript = await build_vibe_vector(prompt_texts, resolved_paths)
        
        idx = get_index()
        
        for attempt in range(3):
            try:
                # Resolve index dimension mismatch dynamically
                stats = idx.describe_index_stats()
                target_dim = stats.get("dimension", 512)
                padded_vector = vector
                if len(padded_vector) < target_dim:
                    padded_vector = padded_vector + [0.0] * (target_dim - len(padded_vector))
                elif len(padded_vector) > target_dim:
                    padded_vector = padded_vector[:target_dim]

                idx.upsert(
                    vectors=[{
                        "id": profile.user_id,
                        "values": padded_vector,
                        "metadata": {"display_name": profile.display_name}
                    }],
                    namespace=PINECONE_NAMESPACE
                )
                break
            except Exception as e:
                if attempt == 2:
                    raise e
                await asyncio.sleep(2 ** attempt)
        
        # Update processing_status to done
        async with AsyncSessionLocal() as db:
            user = await db.get(User, profile.user_id)
            if user:
                user.processing_status = ProcessingStatus.done
                await db.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        # Update processing_status to failed
        async with AsyncSessionLocal() as db:
            user = await db.get(User, profile.user_id)
            if user:
                user.processing_status = ProcessingStatus.failed
                await db.commit()

@router.post("/upload_media")
async def upload_media(file: UploadFile = File(...), user_id: str = Depends(require_auth)):
    try:
        os.makedirs(MEDIA_ROOT, exist_ok=True)
        ext = file.filename.split(".")[-1] if "." in file.filename else "bin"
        unique_filename = f"{uuid.uuid4().hex}.{ext}"
        local_path = os.path.join(MEDIA_ROOT, unique_filename)
        
        # Save locally
        with open(local_path, "wb") as f:
            f.write(await file.read())
            
        # Upload to Supabase
        public_url = await upload_file(local_path, user_id, unique_filename)
        
        return {
            "local_file_path": unique_filename,
            "public_url": public_url
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/ingest_profile")
async def ingest_profile(profile: DeepProfileInput, background_tasks: BackgroundTasks, user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    if profile.user_id != user_id:
        raise HTTPException(status_code=403, detail="Forbidden")

    resolved_paths = []
    for m in profile.media:
        try:
            rp = resolve_safe_media_path(m.local_file_path)
            resolved_paths.append(rp)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid media path")
    
    # Delete existing profile data
    await db.execute(Prompt.__table__.delete().where(Prompt.user_id == user_id))
    await db.execute(Photo.__table__.delete().where(Photo.user_id == user_id))
    
    # Insert new prompts
    for p in profile.prompts:
        db.add(Prompt(user_id=user_id, question=p.question, answer=p.answer))
        
    # Insert new photos/media URLs
    # Assuming public_url is passed back in the MediaItem from the frontend
    for m in profile.media:
        if m.public_url:
            db.add(Photo(user_id=user_id, storage_url=m.public_url))
            
    # Update user status and display name
    user = await db.get(User, user_id)
    if user:
        user.display_name = profile.display_name
        if profile.media and profile.media[0].public_url:
            user.avatar_url = profile.media[0].public_url
        user.processing_status = ProcessingStatus.processing
        
    await db.commit()
    
    background_tasks.add_task(process_and_upsert_profile, profile, resolved_paths)
    return {"status": "queued"}

@router.post("/match")
async def get_matches(req: MatchRequest, db: AsyncSession = Depends(get_db)):
    idx = get_index()
    # Need user vector. Fetch from Pinecone
    try:
        fetch_res = idx.fetch(ids=[req.user_id], namespace=PINECONE_NAMESPACE)
        if not fetch_res.vectors or req.user_id not in fetch_res.vectors:
            # User hasn't set up profile or processing not done. 
            # Gracefully return empty list instead of 500/404 error
            return {"matches": []}
            
        user_vector = fetch_res.vectors[req.user_id].values
        
        query_res = idx.query(
            namespace=PINECONE_NAMESPACE,
            vector=user_vector,
            top_k=req.limit * 2, # Fetch more to account for already liked
            include_metadata=True
        )
        
        # Filter out liked users in DB
        result_ids = [m.id for m in query_res.matches if m.id != req.user_id]
        if not result_ids:
            return {"matches": []}
            
        likes_result = await db.execute(
            select(Like.to_user_id)
            .where(Like.from_user_id == req.user_id)
            .where(Like.to_user_id.in_(result_ids))
        )
        liked_ids = set(likes_result.scalars().all())
        
        # Keep only unliked users up to limit
        unliked_matches = [m for m in query_res.matches if m.id not in liked_ids and m.id != req.user_id][:req.limit]
        if not unliked_matches:
            return {"matches": []}
            
        unliked_ids = [m.id for m in unliked_matches]
        
        # Fetch rich profiles from PostgreSQL
        users_res = await db.execute(select(User).where(User.id.in_(unliked_ids)))
        users = {u.id: u for u in users_res.scalars().all()}
        
        prompts_res = await db.execute(select(Prompt).where(Prompt.user_id.in_(unliked_ids)))
        prompts_by_user = {uid: [] for uid in unliked_ids}
        for p in prompts_res.scalars().all():
            prompts_by_user[p.user_id].append({"question": p.question, "answer": p.answer})
            
        photos_res = await db.execute(select(Photo).where(Photo.user_id.in_(unliked_ids)))
        photos_by_user = {uid: [] for uid in unliked_ids}
        for p in photos_res.scalars().all():
            photos_by_user[p.user_id].append(p.storage_url)
            
        final_profiles = []
        for m in unliked_matches:
            uid = m.id
            if uid not in users:
                continue # Edge case: in pinecone but not db
            u = users[uid]
            final_profiles.append({
                "id": u.id,
                "displayName": u.display_name,
                "avatarUrl": u.avatar_url,
                "photos": photos_by_user.get(uid, []),
                "prompts": prompts_by_user.get(uid, []),
                "score": m.score
            })
            
        return {"matches": final_profiles}
    except Exception as e:
        # Avoid breaking the whole app if pinecone errors, just return empty list
        return {"matches": []}

@router.post("/like")
async def like_user(req: LikeRequest, user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    new_like = Like(
        from_user_id=user_id,
        to_user_id=req.to_user_id,
        target_type=req.target_type,
        target_id=req.target_id,
        comment_text=req.comment_text
    )
    db.add(new_like)
    
    # Check reciprocal
    recip = await db.execute(
        select(Like)
        .where(Like.from_user_id == req.to_user_id)
        .where(Like.to_user_id == user_id)
    )
    if recip.scalars().first():
        match = MatchRecord(user_a_id=user_id, user_b_id=req.to_user_id, status=MatchStatus.matched)
        db.add(match)
        await db.commit()
        return {"matched": True, "match_id": match.id}
        
    await db.commit()
    return {"matched": False}

@router.get("/likes/received")
async def get_received_likes(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Like).where(Like.to_user_id == user_id)
    )
    return {"likes": result.scalars().all()}

@router.get("/matches")
async def get_all_matches(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(MatchRecord)
        .where(or_(MatchRecord.user_a_id == user_id, MatchRecord.user_b_id == user_id))
        .where(MatchRecord.status == MatchStatus.matched)
    )
    match_records = result.scalars().all()
    
    if not match_records:
        return {"matches": []}
    
    # Batch fetch all peer users in a single query
    peer_ids = set()
    match_to_peer = {}
    for mr in match_records:
        peer_id = mr.user_a_id if mr.user_b_id == user_id else mr.user_b_id
        peer_ids.add(peer_id)
        match_to_peer[mr.id] = peer_id
    
    peers_res = await db.execute(select(User).where(User.id.in_(peer_ids)))
    peers = {u.id: u for u in peers_res.scalars().all()}
    
    matches = []
    for mr in match_records:
        peer_id = match_to_peer[mr.id]
        peer = peers.get(peer_id)
        
        matches.append({
            "id": mr.id,
            "peerId": peer_id,
            "avatarUrl": peer.avatar_url if peer else "",
            "displayName": peer.display_name if peer else "Unknown",
            "isNew": False,
            "createdAt": mr.created_at.timestamp() * 1000 if mr.created_at else 0
        })
    
    return {"matches": matches}
