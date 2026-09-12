from datetime import datetime, timedelta
from jose import jwt, JWTError
from passlib.context import CryptContext
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field

from backend.config import JWT_SECRET_KEY, JWT_ALGORITHM, JWT_EXPIRY_HOURS
from backend.database import get_db, User

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
router = APIRouter(prefix="/api/auth", tags=["auth"])

def hash_password(password: str):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(user_id: str, email: str):
    expire = datetime.utcnow() + timedelta(hours=JWT_EXPIRY_HOURS)
    to_encode = {"sub": user_id, "email": email, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

async def require_auth(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

def verify_ws_token(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=128)

@router.post("/register")
async def register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    email = req.email.strip().lower()
    display_name = req.display_name.strip()
    result = await db.execute(select(User).where(User.email == email))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_pwd = hash_password(req.password)
    user = User(email=email, password_hash=hashed_pwd, display_name=display_name)
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Email already registered")
    await db.refresh(user)
    
    token = create_access_token(user.id, user.email)
    return {"access_token": token, "token_type": "bearer"}

@router.post("/login")
async def login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    email = req.email.strip().lower()
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalars().first()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    
    token = create_access_token(user.id, user.email)
    return {"access_token": token, "token_type": "bearer"}

@router.get("/me")
async def get_me(user_id: str = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    from backend.database import Prompt, Photo
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    prompts_result = await db.execute(select(Prompt).where(Prompt.user_id == user_id))
    prompts = [{"question": p.question, "answer": p.answer} for p in prompts_result.scalars().all()]
    
    photos_result = await db.execute(select(Photo).where(Photo.user_id == user_id))
    photos = [p.storage_url for p in photos_result.scalars().all()]
    
    return {
        "id": user.id, 
        "email": user.email, 
        "display_name": user.display_name, 
        "avatar_url": user.avatar_url,
        "prompts": prompts,
        "photos": photos
    }
