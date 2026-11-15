FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

COPY pyproject.toml ./
COPY iiot_sim ./iiot_sim
RUN pip install --no-cache-dir .

COPY config ./config

# Never run as root inside the container.
RUN useradd --uid 10001 --no-create-home sim
USER sim

CMD ["python", "-m", "iiot_sim.run_devices"]
