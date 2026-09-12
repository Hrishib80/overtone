import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ENVIRONMENT = os.environ.get("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production"

PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
INDEX_HOST = os.environ.get("INDEX_HOST")
PINECONE_NAMESPACE = os.environ.get("PINECONE_NAMESPACE", "campus-vibe")
DATABASE_URL = os.environ.get("DATABASE_URL")
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", "media_uploads"))
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
JWT_EXPIRY_HOURS = int(os.environ.get("JWT_EXPIRY_HOURS", "24"))
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
SUPABASE_BUCKET = os.environ.get("SUPABASE_BUCKET", "profile-media")

ALLOWED_ORIGINS = [
    origin.strip().rstrip("/")
    for origin in os.environ.get("ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]

required = ["PINECONE_API_KEY", "INDEX_HOST", "DATABASE_URL", "JWT_SECRET_KEY", "SUPABASE_URL", "SUPABASE_KEY"]
for req in required:
    if not globals().get(req):
        message = f"{req} is not set."
        if IS_PRODUCTION:
            raise RuntimeError(message)
        print(f"WARNING: {message}")

if IS_PRODUCTION and "*" in ALLOWED_ORIGINS:
    raise RuntimeError("ALLOWED_ORIGINS must list explicit HTTPS origins in production.")
