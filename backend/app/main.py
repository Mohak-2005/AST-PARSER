import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings, ENV_PATH
from dotenv import load_dotenv

load_dotenv(dotenv_path=ENV_PATH)

from app.routes.analyze import router as analyze_router
from app.routes.graph import router as graph_router
from app.db.database import create_pool, close_pool, create_tables

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up backend...")
    await create_pool()
    await create_tables()
    logger.info("Backend ready")
    yield
    await close_pool()
    logger.info("Backend shut down")

app = FastAPI(title="AST Dependency Graph Analyzer", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(analyze_router)
app.include_router(graph_router)

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 5000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
