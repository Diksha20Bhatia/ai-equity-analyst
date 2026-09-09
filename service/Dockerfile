# service/Dockerfile — builds the approval microservice as a real,
# independent container image, separate from run.py/dashboard.py.
#
# Build from the REPO ROOT (not this directory), so it can copy the
# rest of the codebase this service imports into (approve_run.py,
# alerts/, config.py, schemas.py, observability.py):
#
#   docker build -f service/Dockerfile -t ai-equity-analyst-approval-service .
#   docker run -p 8000:8000 --env-file .env ai-equity-analyst-approval-service
#
# Installs the full requirements.txt rather than a hand-picked subset —
# a deliberate simplicity-over-minimalism tradeoff for this reference
# build (guarantees every transitive import this service touches via
# config.py/observability.py actually resolves); a real production split
# would trim this to only what service/approval_service.py's own import
# chain needs (fastapi, uvicorn, requests, pydantic, python-dotenv).

FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The service needs the whole repo, not just service/ — it imports
# approve_run.py and alerts/ from the root (see approval_service.py's
# own docstring: it deliberately reuses that code rather than
# re-implementing it).
COPY . .

EXPOSE 8000

# No secrets baked into the image — pass real values with `docker run
# --env-file .env` (or your real deployment platform's own secret store).
CMD ["uvicorn", "service.approval_service:app", "--host", "0.0.0.0", "--port", "8000"]
