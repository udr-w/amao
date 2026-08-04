FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m venv /venv \
    && /venv/bin/pip install --no-cache-dir --upgrade pip \
    && /venv/bin/pip install --no-cache-dir .

FROM python:3.12-slim

# GitHelper/apply_diff shell out to the git binary -- it is a hard runtime dependency.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 amao
COPY --from=builder /venv /venv

USER amao
WORKDIR /workspace
ENV PATH="/venv/bin:$PATH"

ENTRYPOINT ["amao"]
CMD ["--help"]
