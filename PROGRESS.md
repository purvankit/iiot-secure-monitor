# PROGRESS.md: project state for new sessions

> Read this first, then `CLAUDE.md` (the brief). Update this file at the end of every session.
> Last updated: 2026-11-26 (M5 done).

## Status at a glance

| Milestone | State | Evidence |
|---|---|---|
| M0 AWS safety | Done | `iiot` profile = dedicated iiot account (ID in backend.hcl, not in git); $5 budget lives in the org management account |
| M1 Simulator + PLC + gateway (local) | Done, committed `7a5d2b7` | 21 unit + 3 broker tests pass; guide in `simulator/README.md` |
| M2 IoT Core, SQS, S3 (Terraform) | Done, committed `2c20559` | 69/69 msgs in SQS + S3; 5 AWS policy tests pass; guide in `infra/README.md` |
| M3 Detection service + DynamoDB + SNS | Done (2026-11-26) | 58 tests pass; live: 0 false alerts on 90-msg baseline, all 6 attack types detected; results + limitations in `service/README.md` |
| M4 Containerise, ECR + ECS Fargate, CloudWatch | Done (2026-11-26) | M4 specs green; live: 47/47 msgs stored by the Fargate task, 0 alerts; forced error → both alarms → SNS |
| M5 GitHub Actions (CI/CD, OIDC) | Done (2026-11-26), PR #1 → `c49c16a` | ci + deploy green on main; merge → approve → build/scan/push/apply; boundary verified live (see below) |
| **M6 Dashboards, attack demos, README** | **Next** | see below |
| S1 | Not started | see CLAUDE.md §6 |

## M4 results (2026-11-26)

Live test, worker on Fargate with the least-privilege task role, image `dev-0777c8f-dirty-2` (0777c8f = `bdd2f41` after the 2026-11-26 history rewrites)
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

## M5 results (2026-11-26)

- Decisions (owner, with pros/cons): Alpine + `apk upgrade` with a strict Trivy gate; apply role =
  PowerUser + IAM only on `iiot-*` roles carrying the workload permissions boundary; commit emails
  rewritten to the GitHub noreply address (all of main, incl. the first commit);
  strict `protect-main` ruleset (PR + 4 checks, no bypass); squash merges.
- Trivy 0.70 HIGH/CRITICAL: python:3.12-slim 54 (53 unfixed), slim + apt upgrade 53 (all unfixed),
  python:3.12-alpine 16 (all krb5-libs, fixed), Alpine + apk upgrade **0**. ECR basic scan of the
  CI-built image tagged `ec27e0f…` (pre-rewrite SHA of `c49c16a`): COMPLETE, 0 findings.
- PR #1 first run: CI 3/3 green; plan failed AssumeRoleWithWebIdentity (immutable OIDC subject, see
  lessons), fixed in `infra/ci`, re-run 4/4 green. Plan logs: 0 unmasked account IDs.
- Merge → deploy waited for approval → image pushed (single manifest) → apply 1 add/1 change/1 destroy
  (new task definition revision), service stayed at 0.
- Boundary live checks: telemetry 22 → SQS 22 (+1 from tests) + S3 22, rule errors 0; IoT auth
  failures logged (3), 5 AWS security tests pass; demo via Run workflow (desired_count 1): task
  HEALTHY on the CI image, registry loaded, 24 published → 24 readings, queue drained; 1 malformed
  message → `R0.malformed` alert → "emailed" (task role → KMS-encrypted SNS works). Scaled back to 0.

## Next: M6 (dashboards, attack demos, README)

0. Make `tests/spec -m m6` green; then add `m6` to the spec step in `.github/workflows/ci.yml`.
1. Everything goes through PRs now (main is protected): branch → PR → checks → squash merge.
2. Grafana dashboards (`dashboards/*.json`) may only query metrics `worker.py` exports
   (`iiot_messages_total`, `iiot_alerts_total`, `iiot_processing_seconds_*`).
3. README sections: architecture, threat model, IEC 62443 table (FR/SR numbers), cost, demo GIF,
   limitations. No "100%" / "accuracy" claims (spec); only measured numbers.
4. Attack demo against the Fargate worker: Run workflow desired_count 1 → `attacks/run_attack.py all`
   (stop the simulator first) → Run workflow 0. Expect ~1 email per (device, rule) per 5 min.
5. Local cleanup the owner may do: delete branches `backup/pre-email-rewrite` and
   `backup/pre-claude-rewrite` (old history with Gmail / attribution trailers; never push them).

## Tests (how to know nothing is broken)

| Suite | Command (from repo root) | State now |
|---|---|---|
| Simulator | `cd simulator && ../.venv/bin/pytest -q` | 21 pass |
| Service | `cd service && ../.venv/bin/pytest -q` | 66 pass |
| Repo guardrails + attacks | `.venv/bin/pytest -q tests --ignore=tests/spec` | 25 pass |
| Terraform modules (offline mocks) | `terraform -chdir=infra/modules/<m> test` for iot, ingest, data, alerts, compute, ci_oidc | 25 pass |
| M4 + M5 specs | `.venv/bin/pytest -q tests/spec -m "m4 or m5"` (needs Docker) + `terraform -chdir=tests/spec/tf test` | 27 + 10 pass |
| CI (all of the above) | GitHub Actions `ci` on every PR/push; `deploy` plans on PRs | green on main |
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

- **History rewrite on 2026-11-26** (owner's choice): every commit on main lost its attribution
  trailer and the Gmail author, so all hashes changed. ECR image tags made before that (`ec27e0f…`,
  `dev-0777c8f-dirty*`) are pre-rewrite SHAs. The next deploy tags images with current SHAs again.
- **No AI-assistant attribution** in commits, PRs or comments (owner rule, 2026-11-26).

- **GitHub OIDC `sub` may use the immutable form** `repo:owner@ownerId/name@repoId:...` (repo setting
  `use_immutable_subject`). A trust policy on `repo:owner/name:...` fails with "Not authorized to
  perform sts:AssumeRoleWithWebIdentity". Check `gh api repos/OWNER/NAME/actions/oidc/customization/sub`.
  That error = trust-condition mismatch, not missing permissions.
- **Pin actions to SHAs and the Trivy binary version**: trivy-action tags were hijacked in March 2026
  (CVE-2026-33634; v0.69.4-0.69.6 binaries malicious). Pinned: trivy-action v0.36.0 `ed142fd`, Trivy v0.70.0.
- **New ruff releases add default rules**: root `ruff.toml` fixes the rule set, `tests/requirements.txt` pins ruff.
- Don't apply `infra/envs/dev` from the laptop any more (CI owns it). `infra/ci` is owner-only, laptop-only.
- A new AWS call in the worker needs the task-role policy AND the boundary (`modules/ci_oidc`, apply
  `infra/ci` first), or it gets AccessDenied in AWS while all offline tests pass.

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
- M5 `infra/ci` (state key `ci/terraform.tfstate`, 8 resources, free): GitHub OIDC provider, roles
  `github-iiot-plan` / `github-iiot-apply`, policy `iiot-workload-boundary` (attached to the 4 project roles).
- GitHub: environment `dev` (required reviewer = owner, branch `main`), secrets `AWS_PLAN_ROLE_ARN`,
  `TF_STATE_BUCKET`, `ALERT_EMAIL` + env secret `AWS_APPLY_ROLE_ARN`, ruleset `protect-main`.
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
- Wants pros/cons for every real choice (2026-11-26) and a recommendation. Deploy approvals are done by the owner on GitHub (never approve on their behalf).
