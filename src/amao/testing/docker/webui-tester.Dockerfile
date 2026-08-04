# Pre-bakes Chromium + selenium so PythonWebUIStrategy doesn't pay an
# apt-get install cost (observed: ~14 minutes for chromium + its transitive
# deps on a bare python:3.12-slim) on every single test run. Built once, on
# first use, by amao.testing.image_builder; every run after that reuses the
# cached image via a normal `docker run` in seconds. Never published to any
# registry -- local-only, tagged amao-webui-tester:local.
FROM python:3.12-slim

RUN apt-get update -qq \
    && apt-get install -y -qq --no-install-recommends chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --quiet selenium behave
