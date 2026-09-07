# AST Dependency Graph Analyzer

Analyzes a local software project using Tree-sitter, generates a dependency graph via LLM, and visualizes it in a web UI.

## Architecture

```
React Web App (port 3000)
  ↕ HTTP
Local Agent (port 3001)   ←— accesses your local filesystem
  ↕ HTTP
FastAPI Backend (port 5000)
  ↕ HTTP
OpenRouter LLM  +  PostgreSQL
```

---

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Node.js | 18+ | `node --version` |
| Python | 3.10+ | `python --version` |
| Docker Desktop | any | For PostgreSQL only |
| OpenRouter API key | — | https://openrouter.ai |

---

## Quick Start

### 1. Start PostgreSQL

```powershell
docker-compose up -d
```

This starts a PostgreSQL 16 instance at `localhost:5432` with database `dependency_graph`.

---

### 2. Configure the Backend

Edit `backend/.env` and fill in your credentials:

```env
OPENROUTER_API_KEY=your_actual_openrouter_api_key
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=meta-llama/llama-3.3-70b-instruct
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dependency_graph
PORT=5000
```

> If you used the Docker Compose above, the DATABASE_URL above will work as-is.

---

### 3. Start the Backend

```powershell
cd backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
```

The backend will:
- Connect to PostgreSQL and create tables on startup
- Be ready at http://localhost:5000
- Serve API docs at http://localhost:5000/docs

---

### 4. Start the Local Agent

```powershell
cd agent
npm start
```

The agent will be ready at http://localhost:3001.

> **First run**: The `postinstall` script downloads Tree-sitter WASM grammar files to `agent/wasm/`. This requires an internet connection. Subsequent runs use cached files.

---

### 5. Start the Web App

```powershell
cd web
npm run dev
```

Open http://localhost:3000 in your browser.

---

## Usage

1. Open http://localhost:3000
2. Enter the **full path** to any JS/TS project on your machine
   - Example: `C:\Projects\my-react-app`
   - Example: `C:\Users\Mohak\Projects\express-api`
3. Click **Start Analysis**
4. Watch the progress panel as your project is scanned, parsed, and analyzed
5. View the interactive dependency graph
6. Click any **node** to see its source location and metadata
7. Click any **edge** to see the relationship evidence

---

## Output Files

For every analyzed project, the agent writes to `C:\Users\Mohak\Desktop\test\`:

```
C:\Users\Mohak\Desktop\test\
└── <project-folder-name>\
    ├── <project-name>_parse\
    │   ├── src_App_jsx_a3f9c1.json      ← per-file AST artifacts
    │   ├── src_api_client_js_b2e4f1.json
    │   └── ...
    └── repository-code-map.json          ← full Code Map sent to backend
```

Configure via `agent/.env`:
```env
ANALYSIS_OUTPUT_DIR=C:\Users\Mohak\Desktop\test
```

---

## Supported Languages

| Language | Extension |
|---|---|
| JavaScript | `.js` |
| JSX | `.jsx` |
| TypeScript | `.ts` |
| TSX | `.tsx` |

---

## Ignored Directories

The scanner skips: `node_modules`, `.git`, `dist`, `build`, `coverage`, `.cache`, `.next`, `out`, `target`, `vendor`

Configure via `IGNORE_DIRS` in `agent/.env`.

---

## Project Structure

```
ast-parser/
├── agent/                  # Local Agent — Node.js
│   ├── src/
│   │   ├── server.js       # Express HTTP server (port 3001)
│   │   ├── analysisRunner.js
│   │   ├── scanner.js
│   │   ├── filter.js
│   │   ├── languageDetector.js
│   │   ├── parser.js       # web-tree-sitter (WASM, no native build needed)
│   │   ├── extractor.js    # AST traversal + info extraction
│   │   ├── artifactWriter.js
│   │   ├── codeMapBuilder.js
│   │   └── backendClient.js
│   ├── scripts/
│   │   └── downloadWasm.js # Postinstall: downloads .wasm grammar files
│   ├── wasm/               # Auto-created: tree-sitter grammar WASM files
│   ├── package.json
│   └── .env
│
├── backend/                # Backend API — Python + FastAPI
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── routes/
│   │   │   ├── analyze.py  # POST /api/analyze
│   │   │   └── graph.py    # GET /api/graph/:id, GET /api/projects
│   │   ├── services/
│   │   │   ├── llm_service.py
│   │   │   ├── graph_validator.py
│   │   │   └── db_repository.py
│   │   ├── models/
│   │   │   └── schemas.py
│   │   └── db/
│   │       └── database.py
│   ├── requirements.txt
│   └── .env
│
├── web/                    # Frontend — React + Vite
│   ├── src/
│   │   ├── App.jsx
│   │   ├── components/
│   │   │   ├── ProjectSelector.jsx
│   │   │   ├── ProgressPanel.jsx
│   │   │   ├── GraphView.jsx    # React Flow + dagre layout
│   │   │   ├── NodeDetails.jsx
│   │   │   ├── EdgeDetails.jsx
│   │   │   └── ProjectList.jsx
│   │   └── api/
│   │       └── client.js
│   ├── package.json
│   └── .env
│
├── docker-compose.yml      # PostgreSQL only
└── README.md
```

---

## API Reference

### Local Agent (port 3001)

| Method | Path | Description |
|---|---|---|
| GET | `/agent/health` | Health check |
| GET | `/agent/status` | Current analysis stage + progress |
| POST | `/agent/analyze` | Start analysis `{projectPath: "..."}` |

### Backend (port 5000)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Health check |
| POST | `/api/analyze` | Receive Code Map → generate + store graph |
| GET | `/api/graph/{id}` | Get stored graph by project ID |
| GET | `/api/projects` | List all analyzed projects |
| GET | `/docs` | Interactive Swagger UI |

---

## Troubleshooting

### Agent won't start — WASM files missing
Run manually: `node scripts/downloadWasm.js` inside the `agent/` directory.

### Backend fails to connect to PostgreSQL
Make sure Docker is running and `docker-compose up -d` was executed. Verify with `docker ps`.

### LLM returns no graph / empty graph
- Verify `OPENROUTER_API_KEY` is set in `backend/.env`
- Check the backend console for LLM error details
- The model `meta-llama/llama-3.3-70b-instruct` is set via `OPENROUTER_MODEL`

### Frontend won't start (esbuild error on Windows)
If `npm install` fails for the frontend, try:
```powershell
npm install --ignore-scripts
node node_modules/esbuild/install.js
```
