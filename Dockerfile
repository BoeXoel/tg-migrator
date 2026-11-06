# syntax=docker/dockerfile:1

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --gid 1000 app \
    && useradd --uid 1000 --gid app --home-dir /app --no-create-home --shell /usr/sbin/nologin app

# Hash-locked install. Everything is a pre-built wheel except pyaes (pure
# Python, sdist-only), which is built offline with the hash-locked setuptools.
COPY requirements-build.txt requirements.txt ./
RUN pip install --require-hashes --only-binary=:all: -r requirements-build.txt \
    && pip install --require-hashes --no-build-isolation --only-binary=:all: --no-binary=pyaes -r requirements.txt

COPY app ./app
COPY main.py .

RUN mkdir -p /app/data /app/sessions /app/downloads /archive \
    && chown -R app:app /app/data /app/sessions /app/downloads /archive

ENV DATA_DIR=/app/data \
    SESSIONS_DIR=/app/sessions \
    DOWNLOADS_DIR=/app/downloads \
    ARCHIVE_ROOT=/archive

USER app
VOLUME ["/app/data", "/app/sessions", "/app/downloads", "/archive"]
ENTRYPOINT ["python", "main.py"]
CMD ["serve"]


# `docker build --target test .` runs the unit tests in the same environment.
FROM runtime AS test
USER root
COPY requirements-dev.txt .
RUN pip install --require-hashes --no-build-isolation --only-binary=:all: --no-binary=pyaes -r requirements-dev.txt
COPY tests ./tests
COPY pytest.ini .
USER app
ENTRYPOINT ["python", "-m", "pytest", "-q"]
CMD []
