# Capability Matcher — Quickstart

Hybrid capability-matching system for the RICAIP Montrac assembly line.
Combines hard-constraint filtering (Phase 1) with GNN ranking (Phase 2) to assign
the best available robot to each process step.

## Prerequisites

- Python 3.11+
- Docker + Docker Compose (for full-stack)
- TypeDB 3.x (optional — only needed for TypeDB integration)

## 1. Local Development (Mode A — no Docker)

```bash
# Clone and create virtualenv
git clone <repo>
cd VibeCoding-homework-02

python3 -m venv .venv && source .venv/bin/activate
pip install -r src/requirements.txt

# Match step 1 of the Maze 106 assembly
PYTHONPATH=src python -m capability_matcher.cli --step 1

# Match all 18 steps, write results to file
PYTHONPATH=src python -m capability_matcher.cli --all-steps --output /tmp/plan.json

# Start the REST API (hot-reload)
PYTHONPATH=src DATA_DIR=./data MODE=A \
  uvicorn capability_matcher.api:app --reload --port 8080
```

API is available at `http://localhost:8080`. Interactive docs at `http://localhost:8080/docs`.

## 2. Full Stack via Docker Compose

```bash
# Core services (TypeDB + capability-matcher)
docker compose up -d

# + TypeDB web query UI (http://localhost:8889)
docker compose --profile studio up -d

# + OPC UA mock server (Mode B testing)
docker compose --profile mock up -d

# + digital twin UI
docker compose --profile full up -d

# Everything at once
docker compose --profile studio --profile mock --profile full up -d
```

Services:

| Service | URL | Purpose | Profile |
|---------|-----|---------|---------|
| capability-matcher | http://localhost:8080 | REST API | *(default)* |
| typedb | localhost:1729 | Knowledge graph (gRPC) | *(default)* |
| typedb-studio-web | http://localhost:8889 | Web query UI for TypeDB | `studio` |
| opcua-mock | opc.tcp://localhost:4840/ricaip | Simulated Montrac OPC UA server | `mock` |
| digital-twin | http://localhost:8765 | Assembly plan UI | `full` |

## 3. Digital Twin (port 8765)

The digital twin runs on port **8765** and is included in the `full` profile:

```bash
docker compose --profile full up -d
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `http://localhost:8765/` | Assembly plan UI (served HTML) |
| `GET` | `http://localhost:8765/state` | Current workcell snapshot (JSON) |
| `POST` | `http://localhost:8765/plan/load` | Load Maze 106 plan `{"mode": "A"}` |
| `POST` | `http://localhost:8765/plan/start` | Start / resume plan execution |
| `POST` | `http://localhost:8765/plan/pause` | Pause execution |
| `POST` | `http://localhost:8765/plan/reset` | Reset all steps to PENDING |
| `POST` | `http://localhost:8765/workcell/estop` | Set e-stop `{"ok": true/false}` |
| `POST` | `http://localhost:8765/workcell/conveyor` | Set conveyor `{"running": true/false}` |
| `WS` | `ws://localhost:8765/ws` | Push stream — sends `DigitalTwinSnapshot` JSON on every state change |

### Typical workflow

```bash
# 1. Load the plan in Mode A (file-based, no OPC UA needed)
curl -s -X POST http://localhost:8765/plan/load \
  -H "Content-Type: application/json" -d '{"mode": "A"}'

# 2. Start execution
curl -s -X POST http://localhost:8765/plan/start

# 3. Inspect live state
curl -s http://localhost:8765/state | python3 -m json.tool

# 4. Simulate e-stop and resume
curl -s -X POST http://localhost:8765/workcell/estop \
  -H "Content-Type: application/json" -d '{"ok": false}'
curl -s -X POST http://localhost:8765/workcell/estop \
  -H "Content-Type: application/json" -d '{"ok": true}'
```

### WebSocket live feed

```bash
# Requires wscat: npm install -g wscat
wscat -c ws://localhost:8765/ws
# → streams DigitalTwinSnapshot JSON on each robot state change
```

The WebSocket sends the full snapshot immediately on connect, then on every state change.
Useful for building dashboards or integration tests that wait for plan completion.

---

## 4. Seed the Knowledge Graph

Requires TypeDB running and `typedb-driver==3.10.0` installed (included in `src/requirements.txt`).
Default credentials for TypeDB CE: `admin` / `password`.

```bash
# Preview TypeQL without connecting
PYTHONPATH=src python src/scripts/seed_kg.py --dry-run

# Load all fixtures (uses TYPEDB_USERNAME / TYPEDB_PASSWORD from env, defaults: admin/password)
PYTHONPATH=src python src/scripts/seed_kg.py \
  --host localhost --port 1729 --db capability_kg

# Override credentials explicitly
TYPEDB_USERNAME=admin TYPEDB_PASSWORD=password \
PYTHONPATH=src python src/scripts/seed_kg.py \
  --host localhost --port 1729 --db capability_kg
```

TypeDB communication is logged to `data/logs/typedb.log` (JSON-lines, rotates at 10 MB).

## 5. Generate Training Data

```bash
# All 5 scenarios, 1000 samples each (5 000 total)
PYTHONPATH=src DATA_DIR=./data \
  python src/scripts/generate_training_data.py --samples 1000

# Single scenario, quick test
PYTHONPATH=src DATA_DIR=./data \
  python src/scripts/generate_training_data.py \
  --scenario MAZE_106_Assembly --samples 200
```

Output: `data/training/{scenario}/samples.jsonl`

## 6. Train the GNN Ranker

```bash
pip install -r src/requirements-ml.txt   # torch + torch_geometric

PYTHONPATH=src DATA_DIR=./data MODEL_DIR=./models \
  python src/scripts/train_gnn.py --epochs 100 --lr 0.0005 --hidden 64
```

Checkpoint saved to `models/gat_ranker.pt`.

## 7. Run Tests

```bash
# Unit tests (no TypeDB / OPC UA needed)
.venv/bin/python -m pytest src/tests/ -v

# Mode A integration tests — 7 use cases from docs/TESTING.md
PYTHONPATH=src python -m scripts.run_mode_a_tests --verbose

# Same, with JUnit XML output (for CI)
PYTHONPATH=src python -m scripts.run_mode_a_tests --junit data/results/junit.xml

# Integration tests (TypeDB must be running)
.venv/bin/python -m pytest src/tests/integration/ -v --typedb
```

## 8. Execution Modes

| Mode | Command | Requires |
|------|---------|---------|
| A — file-based R&D | `MODE=A` | nothing |
| B — planning / dry-run | `MODE=B` | OPC UA mock or real |
| C — live execution | `MODE=C OPCUA_ENDPOINT=opc.tcp://<plc>:4840` | real testbed |

## 9. Environment Variables

See `.env.example` for all variables with descriptions.
Copy to `.env` for local overrides (never commit `.env`).
