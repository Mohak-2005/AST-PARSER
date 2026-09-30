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

## Quick Start (Automated)

On Windows, you can set up and run everything in two steps:

1. **Initial Setup (run once):**
   Double-click `setup.bat` (or run `./setup.bat` in terminal). This installs dependencies for Agent, Backend, and Web, creates default `.env` files, and sets up Python `venv`.
2. **Start All Services:**
   Double-click `start_all.bat`. This starts PostgreSQL, the Local Agent (3001), Backend API (5000), and Frontend (3000), then automatically opens your browser at http://localhost:3000.

---

## Quick Start (Manual)

### 1. Start PostgreSQL

```powershell
docker-compose up -d
```

This starts a PostgreSQL 16 instance at `localhost:5432` with database `dependency_graph`.

---

### 2. Configure the Backend

Edit `backend/.env` and fill in your credentials:

```env
# Provider options: groq | gemini | hf | mistral | openrouter
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/dependency_graph
PORT=5000
```

---

### 3. Start the Backend

```powershell
cd backend
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
```

---

### 4. Start the Local Agent

```powershell
cd agent
npm start
```

The agent will be ready at http://localhost:3001.

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
2. Enter the **full path** to any project on your machine (e.g. `C:\Projects\my-app`)
3. Click **Start Analysis**
4. View the interactive dependency graph, generated feature contexts, test cases, and test execution results.

---

## Output Files

For every analyzed project, the agent writes to `./analysis_output/` (or the directory configured in `ANALYSIS_OUTPUT_DIR`):

```
analysis_output/
└── <project-folder-name>/
    ├── <project-name>_parse/
    │   ├── src_App_jsx_a3f9c1.json      ← per-file AST artifacts
    │   └── ...
    ├── repository-code-map.json          ← full Code Map sent to backend
    ├── feature_contexts/                 ← feature contexts
    ├── unit_test_cases/                  ← generated test cases
    ├── test_scripts/                     ← generated test scripts
    └── execution_results/                ← test execution output & logs
```

Configure via `agent/.env`:
```env
ANALYSIS_OUTPUT_DIR=./analysis_output
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
