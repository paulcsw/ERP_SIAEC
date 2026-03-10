from fastapi import FastAPI
from sqlalchemy import text

from app.database import engine


def create_app() -> FastAPI:
    app = FastAPI(title="CIS ERP", version="0.1.0")

    @app.get("/health")
    async def health():
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
