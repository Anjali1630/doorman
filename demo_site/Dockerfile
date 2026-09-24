# NOTE ON VALIDATION: written and reviewed, not runtime-built with
# `docker build` itself (no Docker daemon in the build sandbox) - see
# backend/Dockerfile's note and README "Known limitations". This file was
# corrected during review: an earlier draft referenced a nonexistent
# demo_site/requirements.txt; the demo site only needs a subset of
# backend/requirements.txt (FastAPI/uvicorn/Jinja2/itsdangerous/
# python-multipart), so it reuses that single pinned file rather than
# maintaining a second one that could drift out of sync.
FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt /app/backend-requirements.txt
RUN pip install --no-cache-dir -r /app/backend-requirements.txt

COPY demo_site/ /app/demo_site/

EXPOSE 8001
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/api/status', timeout=2)" || exit 1

CMD ["uvicorn", "demo_site.main:app", "--host", "0.0.0.0", "--port", "8001"]
