# PROGRESS.md: project state for new sessions

> Read this first, then `CLAUDE.md` (the brief). Update this file at the end of every session.
> Last updated: 2026-11-26.

## Status at a glance

| Milestone | State | Evidence |
|---|---|---|
| M0 AWS safety | Done | `iiot` profile = dedicated iiot account (ID in backend.hcl, not in git); $5 budget lives in the org management account |
| M1 Simulator + PLC + gateway (local) | Done, committed `f4cd2aa` | 21 unit + 3 broker tests pass; guide in `simulator/README.md` |
| M2 IoT Core, SQS, S3 (Terraform) | Done, committed `044bfc9` | 69/69 msgs in SQS + S3; 5 AWS policy tests pass; guide in `infra/README.md` |
| **M3 Detection service + DynamoDB + SNS** | **In progress, NOT committed** | infra applied; 56/57 tests pass (1 waits on TODO below) |
| M4–M6, S1 | Not started | see CLAUDE.md §6 |

## Resume M3 here (in order)

1. **Pending `TODO(human)`** in `service/iiot_monitor/rules.py` → `_replay()`, branch
   `elif t.session < st.session:`. The owner is writing this (learning exercise); don't fill it
   in unasked. Spec = two tests in `service/tests/test_rules.py`:
   `test_r4_replay_from_previous_session` (must alert, currently failing) and
   `test_r4_late_delivery_from_previous_session_is_not_replay` (must not alert).
   Idea: a real replay is a *new* publish, so broker `t.ingest_ts` is after the current session
   start `st.session`; a late-delivered old message was ingested before it. Allow for device
   clock skew via `self.cfg.future_ms` (owner decides the direction).
2. Run `cd service && ../.venv/bin/pytest -q` → expect 57 passed. Then `ruff check .`.
3. Clear the 62 junk alerts from the backlog run in DynamoDB `iiot-alerts` (ask first).
4. Re-run the worker against SQS (see "Run the service" below), then attack demos:
   spoofed value (R2), flood (R3), replay (R4), impersonation/foreign topic (R1). Confirm alerts
   in DynamoDB + email. Record only measured numbers. `attacks/` scripts don't exist yet.
5. Commit M3 (owner approves commits). Then write a short M3 section in a README.

## Lessons learned (don't repeat these)

- **SQS standard queues reorder and redeliver.** Never assume time order. Backlog replay on
  2026-11-15 produced 41 false R4.old_session + 19 false R3.flood alerts and ~12 emails.
  Fixed: flood window keeps sorted arrivals; R2 baseline only moves forward in time; email
  cooldown uses wall-clock time, not message time. Regression tests use a shuffled 2-session
  backlog (`backlog()` in test_rules.py).
- **Broker fields are trusted, payload fields are not.** The IoT rule adds `mqtt_client_id`,
  `topic_device_id`, `ingest_ts`. Detection timing uses `ingest_ts`.
- **IoT Core unauthorised publish:** MQTT 5 → PUBACK `0x87 Not authorized` (connection stays);
  MQTT 3.1.1 → disconnect. Both logged in `AWSIotLogsV2` as `AUTHORIZATION_FAILURE`.
- **Known limitation:** clients without a valid cert fail at the TLS handshake and are never
  logged; prevention covers them, R1 only covers authenticated misuse (documented in infra/README).
- **Measured clock skew** device→broker (2026-11-15, n=69): median 1.5 s, max 2.8 s (WSL clock
  ~2 s behind). R4 stale threshold 30 s, future 5 s.
- **Org SCP region lock** ("AdvancedModeRegionRestriction" policy in the org management account): only
  ap-south-1, ap-southeast-2, us-east-1 (global services only), us-west-2 allowed. An
  "explicit deny in a service control policy" error means check this first. Never use us-east-1
  for workloads.
- `aws login` credentials need `botocore[crt]` locally (in service dev deps). Fargate won't.
- AWS device keys are `600`, so run the simulator against AWS from the host venv, not Docker.

## Where things are

```
simulator/   devices, Modbus PLC sim, edge gateway (iiot_sim/), tests, README (M1)
service/     detection service (iiot_monitor/): schema, rules R1-R4, alerts, store, authlog, worker
infra/       bootstrap/ (state bucket, LOCAL state, backed up to S3 bootstrap/ key)
             modules/ iot, ingest, data, alerts;  envs/dev/ (S3 backend), README (M2)
scripts/     gen-local-certs.sh, gen-aws-device-keys.sh, fetch-aws-device-certs.sh
local/       mosquitto config; certs/ and aws-certs/ are gitignored (private keys)
```

Gitignored, never commit: `local/certs/`, `local/aws-certs/`, `*.key`, `infra/envs/*/backend.hcl`,
`infra/envs/*/terraform.tfvars` (holds the alert email), `*.tfstate`.

## AWS resources live now (ap-south-1, tag project=iiot-secure-monitor)

- bootstrap: state bucket `iiot-secure-monitor-tfstate-<acct>` (7 resources)
- dev: 41 resources: 5 Things + certs + policy, IoT logging, topic rule, SQS + DLQ,
  S3 raw archive (30-day expiry), DynamoDB `iiot-readings`/`iiot-latest`/`iiot-alerts`,
  SNS `iiot-alerts` with confirmed email subscription.
- Nothing bills hourly yet. Cost accrues only while the simulator runs.

## Run the service (M3, local, against real AWS)
```bash
cd ~/projects/iiot-secure-monitor
eval "$(AWS_PROFILE=iiot terraform -chdir=infra/envs/dev output -raw service_env)"
AWS_PROFILE=iiot .venv/bin/python -m iiot_monitor.worker
# health/metrics: curl localhost:8080/health ; curl localhost:8080/metrics
```
Simulator against AWS: `infra/README.md` → "Run the simulator against AWS".

## Working with the owner

- Wants explanations of the *why*, plain language for jargon, and occasional quiz questions.
- Uses a phone sometimes: give short, paste-safe commands that start with an absolute `cd`
  (no `\` line continuations). New WSL tabs open in `/mnt/c/Users/Purvankit`, not `~`.
- Confirm before `terraform apply`, commits, deletes, or anything that sends email.
