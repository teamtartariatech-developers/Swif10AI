from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/rag", tags=["rag"])


@router.get("/status")
async def rag_status() -> dict[str, str]:
    raise HTTPException(status_code=501, detail="RAG pipeline not configured")
