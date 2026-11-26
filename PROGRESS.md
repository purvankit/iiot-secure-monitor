# PROGRESS.md: project state for new sessions

> Read this first, then `CLAUDE.md` (the brief). Update this file at the end of every session.
> Last updated: 2026-11-26 (M4 done).

## Status at a glance

| Milestone | State | Evidence |
|---|---|---|
| M0 AWS safety | Done | `iiot` profile = dedicated iiot account (ID in backend.hcl, not in git); $5 budget lives in the org management account |
| M1 Simulator + PLC + gateway (local) | Done, committed `f4cd2aa` | 21 unit + 3 broker tests pass; guide in `simulator/README.md` |
| M2 IoT Core, SQS, S3 (Terraform) | Done, committed `044bfc9` | 69/69 msgs in SQS + S3; 5 AWS policy tests pass; guide in `infra/README.md` |
| M3 Detection service + DynamoDB + SNS | Done (2026-11-26) | 58 tests pass; live: 0 false alerts on 90-msg baseline, all 6 attack types detected; results + limitations in `service/README.md` |
| M4 Containerise, ECR + ECS Fargate, CloudWatch | Done (2026-11-26) | M4 specs green; live: 47/47 msgs stored by the Fargate task, 0 alerts; forced error → both alarms → SNS |
| **M5 GitHub Actions** | **Next** | see below |
| M6, S1 | Not started | see CLAUDE.md §6 |

## M4 results (2026-11-26)

Live test, worker on Fargate with the least-privilege task role, image `dev-0777c8f-dirty-2`
(built from uncommitted M4 code; CI tags will be clean SHAs):
- Task reached RUNNING + HEALTHY; first deploy 4 polls of 15 s, image-swap rollout ~3.5 min
  (old task stops first, by design).
- Happy path: simulator 60 s → 47 messages published → 47 readings in DynamoDB, 0 alerts.
- Forced error (temporary inline Deny on dynamodb:PutItem, 3 messages from fake device
  `fault-injection-01`, Deny removed after): first `processing failed` log 18:38:19 UTC;
  `processing-errors` ALARM 18:39:25; `dlq-not-empty` ALARM 18:43:11; alarm history shows
  "Successfully executed action" to SNS for both. DLQ purged, service back to 0.
- ECR basic scan of the image: 9 HIGH, 5 MEDIUM, 2 UNDEFINED. All 9 HIGH are Debian `openssl`
  3.5.9-1~deb13u1 in `python:3.12-slim`, with no fixed package available yet (checked 2026-11-26).

## Next: M5 (GitHub Actions)

0. Make `tests/spec -m m5` + `tests/spec/tf/tests/m5_ci_oidc.tftest.hcl` green (module `infra/modules/ci_oidc`).
1. **Decide with owner before writing ci.yml:** the frozen spec's Trivy step fails on HIGH, and the
   base image has 9 unfixed HIGH openssl CVEs. Options: `ignore-unfixed: true` (allowed, spec only
   fixes severity + exit-code), or a different base image (distroless/Chainguard). Don't edit the spec.
2. Push with `provenance: false` (docker/build-push-action) or ECR can't scan it (see lessons).
3. Ruff from the repo root reports 6 pre-existing issues in `tests/spec/*` and `tests/test_guardrails.py`
   (RUF017, FURB167), which matter once CI runs ruff. Spec files are frozen: ask the owner, or scope ruff config.
4. ECS deploy in CI = `terraform apply -var image_tag=${{ github.sha }}`; keep `desired_count` 0 by default.

## Tests (how to know nothing is broken)

| Suite | Command (from repo root) | State now |
|---|---|---|
| Simulator | `cd simulator && ../.venv/bin/pytest -q` | 21 pass |
| Service | `cd service && ../.venv/bin/pytest -q` | 66 pass |
| Repo guardrails + attacks | `.venv/bin/pytest -q tests --ignore=tests/spec` | 25 pass |
| Terraform modules (offline mocks) | `terraform -chdir=infra/modules/<m> test` for iot, ingest, data, alerts, compute | 17 pass |
| M4 spec | `.venv/bin/pytest -q tests/spec -m m4` (needs Docker) + tf spec | 12 + 7 pass. `terraform test` loads ALL spec files, so until ci_oidc exists run m4 from a scratch copy (symlink `infra`, copy main.tf + m4 file) |
| **M5 spec** | `.venv/bin/pytest -q tests/spec -m m5` + tf spec (ci_oidc runs) | red until M5 |
| **M6 spec** | `.venv/bin/pytest -q tests/spec -m m6` | red until M6 |

Spec tests were written BEFORE M4-M6 and are frozen (rule in CLAUDE.md). Each was checked on
2026-11-26 to be satisfiable (a throwaway prototype in the scratchpad passed it, then was deleted)
and non-vacuous (deliberately broken prototypes failed it). They fix interfaces M4/M5 must follow:
module `infra/modules/compute` (resources `aws_ecs_service.worker`, `aws_ecs_task_definition.worker`,
`aws_security_group.worker`, `aws_ecr_repository.service`, `aws_iam_role.{task,execution}`,
`aws_iam_role_policy.task`, alarms `processing_errors` + `dlq_not_empty`, `aws_cloudwatch_dashboard.main`;
variables in the spec file), module `infra/modules/ci_oidc` (`plan`/`apply` roles), workflows
`.github/workflows/{ci,deploy}.yml`, `dashboards/*.json`, README sections.
Guardrails: a needed `Resource = "*"` must carry `# guardrail: allow-wildcard (reason)`.

## Lessons learned (don't repeat these)

- **CloudWatch alarms can't publish to an SNS topic encrypted with `alias/aws/sns`** (its key
  policy can't be edited), so the alarm goes to ALARM and no email is sent. Fixed: customer-managed key
  `alias/iiot-alerts` (~$1/month, owner chose it 2026-11-26) whose policy allows cloudwatch.amazonaws.com,
  plus account principals via SNS only (`kms:ViaService`), so the task role needs no KMS permissions.
- **ECR basic scanning can't scan an OCI image index.** BuildKit's default provenance attestation
  turns a push into an index, so scan_on_push silently did nothing. Always `--provenance=false`.
- **Mocked `aws_iam_policy_document` returns random text, not JSON**: use `jsonencode` in modules
  that `terraform test` with mock providers.
- 12-digit runs in mock IDs (like a fake UUID group) trip the account-ID guardrail.

- **SQS standard queues reorder and redeliver.** Never assume time order. Backlog replay on
  2026-11-15 produced 41 false R4.old_session + 19 false R3.flood alerts and ~12 emails.
  Fixed: flood window keeps sorted arrivals; R2 baseline only moves forward in time; email
  cooldown uses wall-clock time, not message time. Regression tests use a shuffled 2-session
  backlog (`backlog()` in test_rules.py).
- **Untrusted messages must not update state.** A message failing R1 used to still update the
  impersonated device's replay/flood state (fixed 2026-11-26, regression test
  `test_r1_impersonation_cannot_poison_victim_state`).
- **Measure latency on one clock.** Laptop clock (~2 s behind AWS) minus broker `ingest_ts`
  gave negative latencies; use attack-script start vs alert `raised_at` (both laptop).
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
- dev: 41 resources (unchanged in the M3 demo): 5 Things + certs + policy, IoT logging, topic rule, SQS + DLQ,
  S3 raw archive (30-day expiry), DynamoDB `iiot-readings`/`iiot-latest`/`iiot-alerts`,
  SNS `iiot-alerts` with confirmed email subscription.
- M4 (+25 resources): customer KMS key for the alerts topic (~$1/month, the only fixed cost), VPC `10.20.0.0/16` with
  2 public subnets + IGW (free), ECR `iiot-monitor` (2 tags, storage cents), ECS cluster `iiot` +
  service `iiot-worker` at **desired_count 0**, log group `/ecs/iiot-worker` (7 d), 2 alarms, dashboard.
- Nothing bills hourly while desired_count = 0. Run guide: `infra/README.md` → "Run the detection service on Fargate".

## Run the service (local, against real AWS)
```bash
cd ~/projects/iiot-secure-monitor
eval "$(AWS_PROFILE=iiot terraform -chdir=infra/envs/dev output -raw service_env)"
AWS_PROFILE=iiot .venv/bin/python -m iiot_monitor.worker
# health/metrics: curl localhost:8080/health ; curl localhost:8080/metrics
```
Simulator against AWS: `infra/README.md` → "Run the simulator against AWS".

## Working with the owner

- Attack demo: `attacks/run_attack.py all` (stop the simulator first; it uses feeder-01's identity).
- Wants explanations of the *why*, plain language for jargon, and occasional quiz questions.
- Uses a phone sometimes: give short, paste-safe commands that start with an absolute `cd`
  (no `\` line continuations). New WSL tabs open in `/mnt/c/Users/Purvankit`, not `~`.
- Confirm before `terraform apply`, commits, deletes, or anything that sends email.
