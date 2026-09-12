import httpx
import mimetypes
from backend.config import SUPABASE_URL, SUPABASE_KEY, SUPABASE_BUCKET

async def upload_file(file_path: str, user_id: str, filename: str) -> str:
    try:
        path = f"{user_id}/{filename}"
        url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{path}"
        
        content_type, _ = mimetypes.guess_type(filename)
        if not content_type:
            content_type = "application/octet-stream"
        
        headers = {
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "apiKey": SUPABASE_KEY,
            "Content-Type": content_type
        }
        async with httpx.AsyncClient() as client:
            with open(file_path, "rb") as f:
                response = await client.post(url, headers=headers, content=f.read())
                response.raise_for_status()
        
        return f"{SUPABASE_URL}/storage/v1/object/public/{SUPABASE_BUCKET}/{path}"
    except Exception as e:
        print(f"Supabase Storage failed, using local media fallback: {e}")
        return f"/media_uploads/{filename}"

async def delete_file(storage_path: str):
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{storage_path}"
    headers = {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apiKey": SUPABASE_KEY
    }
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers)
        response.raise_for_status()
