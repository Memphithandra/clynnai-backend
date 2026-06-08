import uvicorn
from .config import get_settings

settings = get_settings()
uvicorn.run("clynnai_backend.main:app", host=settings.host, port=settings.port, reload=False)
