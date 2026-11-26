# Detection service image. Two stages: build a wheel with the full toolchain, then install
# only that wheel + its runtime deps into a clean slim image (no pip cache, no tests, no dev deps).

# ---- build stage ----
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml ./
COPY iiot_monitor ./iiot_monitor
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime stage ----
FROM python:3.12-slim
# PYTHONDONTWRITEBYTECODE: ECS runs this with a read-only root filesystem, so Python must
# never try to write .pyc files. PYTHONUNBUFFERED: log lines reach CloudWatch immediately.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-compile /wheels/*.whl \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
# Numeric UID: Kubernetes/ECS can verify "non-root" without reading /etc/passwd.
USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "iiot_monitor.worker"]
