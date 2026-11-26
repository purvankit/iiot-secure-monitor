# Detection service image. Two stages: build a wheel with the full toolchain, then install
# only that wheel + its runtime deps into a clean image (no pip cache, no tests, no dev deps).
#
# Alpine instead of Debian slim (decided 2026-11-26 from Trivy 0.70 scans): slim had 54 HIGH
# CVEs, 53 with no fix released, so CI's strict HIGH/CRITICAL gate could never pass; Alpine
# + `apk upgrade` had 0. Alpine uses musl libc: fine for our pure-Python deps (boto3,
# prometheus-client); a future C-extension dependency may need a musllinux wheel or compiling.

# ---- build stage ----
FROM python:3.12-alpine AS build
WORKDIR /src
COPY pyproject.toml ./
COPY iiot_monitor ./iiot_monitor
RUN pip wheel --no-cache-dir --wheel-dir /wheels .

# ---- runtime stage ----
FROM python:3.12-alpine
# PYTHONDONTWRITEBYTECODE: ECS runs this with a read-only root filesystem, so Python must
# never try to write .pyc files. PYTHONUNBUFFERED: log lines reach CloudWatch immediately.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Pull OS security fixes released after the base image was built (it was 16 HIGH in
# krb5-libs on 2026-11-26). Trade-off: builds aren't bit-for-bit reproducible, but every
# build is scanned in CI before it can be pushed.
RUN apk upgrade --no-cache
RUN --mount=type=bind,from=build,source=/wheels,target=/wheels \
    pip install --no-cache-dir --no-compile /wheels/*.whl \
    && adduser -D -H -u 10001 -s /sbin/nologin app
# Numeric UID: Kubernetes/ECS can verify "non-root" without reading /etc/passwd.
USER 10001
EXPOSE 8080
ENTRYPOINT ["python", "-m", "iiot_monitor.worker"]
