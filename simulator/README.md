# Simulator (M1): local run and verification

Four MQTT devices (feeder-01, feeder-02, xfmr-01, motor-01), a Modbus TCP pump-station
PLC, and an edge gateway (edge-gw-01) that polls the PLC and forwards over MQTT.
Locally, Mosquitto stands in for AWS IoT Core: mutual TLS, identity = certificate CN,
and an ACL that lets each device publish only to `iiot/<its CN>/telemetry`.

All commands run from the repo root unless noted.

## 0. One-time setup
```bash
python3 -m venv .venv
.venv/bin/pip install -e './simulator[dev]'
./scripts/gen-local-certs.sh       # local CA + one cert per device
```
Expect `issued feeder-01` … `issued monitor`. Re-running skips existing certs.

## 1. Unit tests (no broker needed)
```bash
cd simulator && ../.venv/bin/pytest -q && ../.venv/bin/ruff check . && cd ..
```
Expect `21 passed, 3 deselected` (the 3 broker tests run in step 5).

## 2. Start the stack
```bash
docker compose up -d --build
docker compose ps
```
Expect 4 running services: mosquitto, devices, plc, gateway.

## 3. Watch the devices publish
```bash
docker compose logs -f devices gateway     # Ctrl+C to stop watching
```
Each of the 5 devices logs a line about every 5 s; `seq=` goes up by 1 each time.

## 4. Subscribe from the broker side
```bash
docker compose exec mosquitto mosquitto_sub -h localhost -p 8883 \
  --cafile /mosquitto/certs/ca.crt --cert /mosquitto/certs/monitor.crt \
  --key /mosquitto/certs/monitor.key -t 'iiot/#' -v
```
Expect JSON on 5 topics, `iiot/feeder-01/telemetry` … `iiot/edge-gw-01/telemetry`.

## 5. Security tests (broker must be running)
```bash
cd simulator && ../.venv/bin/pytest -m integration -v && cd ..
```
Expect 3 passed: a device can publish to its own topic, feeder-01 cannot spoof
motor-01's topic, and a client without a certificate is rejected.

## 6. Try the attacks by hand
```bash
# No client cert: the connection should fail
docker compose exec mosquitto mosquitto_pub -h localhost -p 8883 \
  --cafile /mosquitto/certs/ca.crt -t iiot/motor-01/telemetry -m hack
docker compose logs --tail 3 mosquitto     # "peer did not return a certificate"

# Valid cert, wrong topic: silently dropped. Keep the step-4 subscriber
# running and check that "spoof" never appears.
docker compose exec mosquitto mosquitto_pub -h localhost -p 8883 \
  --cafile /mosquitto/certs/ca.crt --cert /mosquitto/certs/feeder-01.crt \
  --key /mosquitto/certs/feeder-01.key -i spoof-test \
  -t iiot/motor-01/telemetry -m spoof -q 1
```
`-i spoof-test` gives the attacker its own client ID; reusing `feeder-01` would
knock the real feeder-01 off the broker (MQTT session takeover).

## 7. PLC outage handling
```bash
docker compose stop plc;  docker compose logs --tail 3 gateway   # "PLC unreachable"
docker compose start plc; docker compose logs --tail 3 gateway   # readings resume
```

## 8. Stop everything
```bash
docker compose down
```
