# EarningsEdge application folder

All **setup, API keys, and features** for this app are documented in the **repository root**:

**[../README.md](../README.md)**

Quick start from here:

```bash
cd earningsedge
cp .env.example .env
cd backend && pip install -r requirements.txt && uvicorn main:app --reload --port 8000
# other terminal: cd frontend && npm install && npm start
```

For a single-container local build, see **`Dockerfile`** in this folder.
