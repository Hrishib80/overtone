import asyncio
from backend.config import DATABASE_URL
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def main():
    engine = create_async_engine(DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE chat_messages ADD COLUMN message_text TEXT;"))
            print("Added message_text column")
        except Exception as e:
            print(f"Error adding column: {e}")
            
        try:
            await conn.execute(text("ALTER TABLE chat_messages DROP COLUMN encrypted_payload;"))
            print("Dropped encrypted_payload column")
        except Exception as e:
            print(f"Error dropping encrypted_payload: {e}")
            
        try:
            await conn.execute(text("ALTER TABLE chat_messages DROP COLUMN iv;"))
            print("Dropped iv column")
        except Exception as e:
            print(f"Error dropping iv: {e}")

if __name__ == "__main__":
    asyncio.run(main())
