# JobMatcher Backend

A production-ready FastAPI backend for a simple job matching application.

## Project structure

- app/__init__.py: package marker
- app/config.py: application settings
- app/database.py: SQLAlchemy engine and DB session
- app/models.py: ORM models for jobs, candidates, and matches
- app/schemas.py: Pydantic request/response models
- app/services.py: core matching logic
- app/main.py: FastAPI application entrypoint
- tests/test_api.py: smoke tests for health and matching flows

## Install dependencies

```bash
cd /Users/kirtiv/JobMatcherBackend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run locally

```bash
uvicorn app.main:app --reload
```

Then browse to http://127.0.0.1:8000/docs for the interactive Swagger UI.

