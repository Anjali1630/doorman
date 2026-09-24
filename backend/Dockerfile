# Uses Microsoft's official Playwright Python image, which ships Chromium/
# Firefox/WebKit AND all required OS-level dependencies pre-installed for
# the matching Playwright pip version. This avoids the common failure mode
# of `playwright install --with-deps` on a generic slim base image (missing
# apt packages, version drift between the pip package and the downloaded
# browser build). The tag's version (v1.56.0) is kept in lockstep with the
# `playwright` pin in requirements.txt - if you bump one, bump the other.
#
# NOTE ON VALIDATION: this Dockerfile was written and reviewed, and its
# install steps mirror exactly what `scripts/verify_clean_env.sh` performs
# and was ACTUALLY run and verified in a fresh Python venv (see that
# script and README "Known limitations"). It has not been runtime-built
# with `docker build` itself, because no Docker daemon was available in
# the sandbox this project was built in, and mcr.microsoft.com is outside
# that sandbox's network allow-list. If the base image tag above ever
# stops matching an available tag, `docker pull mcr.microsoft.com/playwright/python:v1.56.0-noble`
# will fail fast and loudly at build time, rather than silently falling
# back to something broken - the pip requirements.txt path below is
# unaffected and is the one exercised by verify_clean_env.sh.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble

WORKDIR /app/backend
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Browsers are already bundled in the base image at the matching version,
# but this is a fast, safe no-op if they're already present, and a real
# safety net if a future base-image update ever changes what's bundled.
RUN python -m playwright install chromium

COPY backend/ .
COPY .env.example /app/.env.example

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
