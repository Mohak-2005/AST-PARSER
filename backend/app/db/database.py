import asyncpg
import logging
from app.config import settings

logger = logging.getLogger(__name__)
pool = None

async def create_pool():
    global pool
    pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
    logger.info("Database pool created")
    return pool

async def close_pool():
    global pool
    if pool:
        await pool.close()

def get_pool():
    return pool

CREATE_TABLES_SQL = """
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_name TEXT NOT NULL,
    project_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'analyzing',
    code_map JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    node_id TEXT NOT NULL,
    node_type TEXT NOT NULL,
    name TEXT NOT NULL,
    file_path TEXT,
    metadata JSONB DEFAULT '{}',
    UNIQUE(project_id, node_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_node_id TEXT NOT NULL,
    target_node_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    confidence FLOAT DEFAULT 1.0,
    evidence JSONB DEFAULT '{}',
    metadata JSONB DEFAULT '{}',
    UNIQUE(project_id, source_node_id, target_node_id, relationship)
);
"""

async def create_tables():
    async with pool.acquire() as conn:
        await conn.execute(CREATE_TABLES_SQL)
        await conn.execute("ALTER TABLE projects ADD COLUMN IF NOT EXISTS code_map JSONB DEFAULT '{}';")
    logger.info("Tables created/verified")
