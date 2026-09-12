from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy import Column, String, DateTime, Enum, ForeignKey, Text, Index, Float, Integer, JSON
import enum
from datetime import datetime
import uuid

from backend.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False, pool_size=10, max_overflow=20, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

class ProcessingStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    done = "done"
    failed = "failed"

class MatchStatus(str, enum.Enum):
    pending = "pending"
    matched = "matched"
    passed = "passed"

class ChatStatus(str, enum.Enum):
    sent = "sent"
    delivered = "delivered"
    read = "read"

def generate_uuid():
    return str(uuid.uuid4())

class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=generate_uuid)
    email = Column(String, unique=True, index=True)
    display_name = Column(String)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    processing_status = Column(Enum(ProcessingStatus), default=ProcessingStatus.pending)
    is_moderated_ok = Column(String, default="pending")
    avatar_url = Column(String, nullable=True)

class Prompt(Base):
    __tablename__ = "prompts"
    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    question = Column(String)
    answer = Column(String)

class Photo(Base):
    __tablename__ = "photos"
    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    storage_url = Column(String)
    moderation_passed = Column(String, default="pending")

class ProfileMedia(Base):
    __tablename__ = "profile_media"
    id = Column(String, primary_key=True, default=generate_uuid)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    asset_type = Column(String)
    storage_url = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class ProfileSignal(Base):
    __tablename__ = "profile_signals"
    user_id = Column(String, ForeignKey("users.id"), primary_key=True)
    composite_vector = Column(JSON)
    prompt_vector = Column(JSON, nullable=True)
    audio_vector = Column(JSON, nullable=True)
    visual_vector = Column(JSON, nullable=True)
    transcript = Column(Text, nullable=True)
    quality_score = Column(Float, default=0.0)
    prompt_count = Column(Integer, default=0)
    media_count = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Like(Base):
    __tablename__ = "likes"
    id = Column(String, primary_key=True, default=generate_uuid)
    from_user_id = Column(String, ForeignKey("users.id"), index=True)
    to_user_id = Column(String, ForeignKey("users.id"), index=True)
    target_type = Column(String) # 'prompt' or 'photo'
    target_id = Column(String)
    comment_text = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Comment(Base):
    __tablename__ = "comments"
    id = Column(String, primary_key=True, default=generate_uuid)
    like_id = Column(String, ForeignKey("likes.id"))
    from_user_id = Column(String, ForeignKey("users.id"))
    body = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

class MatchRecord(Base):
    __tablename__ = "match_records"
    __table_args__ = (
        Index('ix_match_records_user_a', 'user_a_id'),
        Index('ix_match_records_user_b', 'user_b_id'),
    )
    id = Column(String, primary_key=True, default=generate_uuid)
    user_a_id = Column(String, ForeignKey("users.id"))
    user_b_id = Column(String, ForeignKey("users.id"))
    status = Column(Enum(MatchStatus), default=MatchStatus.pending)
    created_at = Column(DateTime, default=datetime.utcnow)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        Index('ix_chat_messages_match_id', 'match_id'),
        Index('ix_chat_messages_match_created', 'match_id', 'created_at'),
    )
    id = Column(String, primary_key=True, default=generate_uuid)
    match_id = Column(String, ForeignKey("match_records.id"))
    from_user_id = Column(String, ForeignKey("users.id"))
    message_text = Column(Text)
    status = Column(Enum(ChatStatus), default=ChatStatus.sent)
    created_at = Column(DateTime, default=datetime.utcnow)

class ChatAttachment(Base):
    __tablename__ = "chat_attachments"
    id = Column(String, primary_key=True, default=generate_uuid)
    message_id = Column(String, ForeignKey("chat_messages.id"), unique=True, index=True)
    media_type = Column(String)
    media_url = Column(String)
    mime_type = Column(String)
    duration_ms = Column(Integer, nullable=True)
