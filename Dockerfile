FROM ghcr.io/astral-sh/uv:0.8.14 AS uv

FROM python:3.12.11-slim-bookworm

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential gdal-bin libgdal-dev libgeos-dev libproj-dev proj-data proj-bin \
       pandoc fonts-noto-core fonts-noto-extra \
       libcairo2 libffi-dev libgdk-pixbuf-2.0-0 libpango-1.0-0 libpangoft2-1.0-0 \
       shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /usr/local/bin/
WORKDIR /opt/soia
COPY pyproject.toml uv.lock README.md LICENSE ./
ENV UV_PROJECT_ENVIRONMENT=/opt/soia/.venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH=/opt/soia/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

WORKDIR /workspace
ENTRYPOINT ["soia"]
CMD ["--help"]
