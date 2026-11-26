# Detection service (M3): SQS → rules → DynamoDB + SNS

Python worker that consumes telemetry from SQS, validates it, runs detection rules R1–R4,
stores readings/latest state/alerts in DynamoDB and emails alerts through SNS.
Exposes `/health` and Prometheus `/metrics` on port 8080.

## Rules

| Rule | Detects | Key signal (trusted?) |
|---|---|---|
| R1 identity | unknown device, payload `device_id` ≠ broker client ID/topic, wrong device type, IoT Core `AUTHORIZATION_FAILURE` log events | `mqtt_client_id`, `topic_device_id` added by the IoT rule (trusted); CloudWatch `AWSIotLogsV2` |
| R2 plausibility | values outside physical range; impossible rate of change; invalid breaker state | limits table in `rules.py` (physical/sensor limits, not alarm limits) |
| R3 flood | > 10 messages per device in 10 s (normal: 2), one alert per window | broker `ingest_ts` (trusted), not device `ts` |
| R4 replay | duplicate seq, seq older than a 256-message window, message from an old session published after a reboot, stale (> 30 s) or future (> 5 s) timestamps | `session`/`seq` (GOOSE stNum/sqNum style) + broker `ingest_ts` |

Design points:
- **SQS is at-least-once and unordered.** Duplicate `MessageId`s are skipped (not replays);
  unseen out-of-order seqs are accepted; the flood window and R2 baselines are order-independent.
- **Messages failing R1 never update device state**, so an impersonator can't reset the victim's
  baseline and make its genuine traffic look replayed.
- **Delete-after-process:** a message is deleted from SQS only after it is stored; failures are
  retried and go to the DLQ after 5 attempts. Malformed messages are alerted (`R0.malformed`)
  and deleted, because retrying can't fix them.
- **Email cooldown:** at most one email per (device, rule) per 5 min, by wall-clock time. Every
  alert is still stored in DynamoDB.

## Run locally against AWS
```bash
cd ~/projects/iiot-secure-monitor
.venv/bin/pip install -e './service[dev]'
eval "$(AWS_PROFILE=iiot terraform -chdir=infra/envs/dev output -raw service_env)"
AWS_PROFILE=iiot .venv/bin/python -m iiot_monitor.worker
```
Tests: `cd ~/projects/iiot-secure-monitor/service && ../.venv/bin/pytest -q`

## Attack demo
Stop the simulator first (the attacker uses feeder-01's identity), keep the worker running:
```bash
cd ~/projects/iiot-secure-monitor
export MQTT_HOST=$(terraform -chdir=infra/envs/dev output -raw iot_endpoint)
AWS_PROFILE=iiot .venv/bin/python attacks/run_attack.py all
```
Threat model: the attacker holds feeder-01's **stolen certificate and key** (compromised device).

### Measured results (2026-11-26, one run)

Baseline: 5 devices, 90 s of normal traffic, 90 messages: **0 alerts**.

| Attack | Alerts raised | Detected |
|---|---|---|
| Spoofed value (55 Hz, then +0.9 Hz in 1.1 s) | R2.out_of_range, R2.rate_of_change | yes |
| Flood (50 msgs in ~6 s) | R3.message_flood ×1 | yes |
| Replay of captured genuine message (242 s old) | R4.old_session, R4.stale | yes |
| Byte-identical duplicate | R4.duplicate_seq | yes |
| Impersonation (feeder-01 cert, payload claims motor-01) | R1.identity_mismatch (critical) | yes |
| Publish to motor-01's topic | R1.auth_failure_publish_in (via CloudWatch Logs) | yes |

All 10 alerts were expected ones; 8 emails were sent (cooldown suppressed repeats).

Time from attack start to stored alert, measured on one clock (upper bound, includes TLS
connect): impersonation **0.17 s**, replay **0.70 s** (also includes the S3 capture fetch),
foreign-topic publish **85.7 s** (CloudWatch log delivery + 60 s polling interval).

## Known limitations
- **TLS-level rejections** (no valid cert) never reach IoT Core's logs, so they can't be
  attributed to a device. Prevented, not detected.
- **Reboot blind spot:** an old-session replay published within 5 s (the clock-skew tolerance)
  of a device reboot is not flagged as `old_session` (the 30 s stale check may still catch it).
- **After a service restart** only each device's last seq is restored from `iiot-latest`, so a
  replay of a recent (< 30 s old, within-window) seq right after a restart isn't flagged.
  Persisting the seen-window would close this.
- **Auth-failure alerts take ~1.5 min** because of log delivery and polling. A CloudWatch
  Logs subscription would make them near-real-time.
- State is in memory in one process: running 2+ workers would split per-device state.
- A stolen key can still send plausible, in-sequence data; that is what certificate
  revocation (deactivate the cert in IoT Core) is for.
