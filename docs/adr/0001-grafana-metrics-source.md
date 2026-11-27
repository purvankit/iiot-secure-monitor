# ADR 0001: Where Grafana gets the detection worker's metrics

- **Status:** Accepted, option C (2026-11-27)
- **Date:** 2026-11-27
- **Milestone:** M6 (dashboards, demo GIF, README)

## Problem

M6 requires Grafana dashboards (`dashboards/*.json`) that chart the detection worker's
Prometheus metrics: `iiot_messages_total`, `iiot_alerts_total` and
`iiot_processing_seconds` (the frozen spec `tests/spec/test_m6_docs.py` checks this). The
worker exposes them on `http://<task>:8080/metrics`.

Prometheus normally **pulls**: it visits `/metrics` every 15 s and stores the history; Grafana
charts that history. The production worker runs on ECS Fargate in a deliberately closed
network, so nothing can pull from it. The worker could **push** instead, but its AWS
permissions are frozen by the M4 spec. Something has to give.

## Hard constraints

| # | Constraint | Source |
|---|---|---|
| C1 | Task role = **exactly** 7 actions (`sqs:ReceiveMessage`, `sqs:DeleteMessage`, `dynamodb:PutItem`, `dynamodb:Scan`, `sns:Publish`, `iot:ListThings`, `logs:FilterLogEvents`) | Frozen M4 spec (`m4_compute.tftest.hcl`) |
| C2 | Task size 0.25 vCPU / **512 MB**, shared by every container in the task | Frozen M4 spec |
| C3 | Worker security group: **no inbound** rules, egress TCP 443 only; no NAT, no load balancer | Frozen M4 spec, cost rules |
| C4 | Worker container is `container_definitions[0]`: exactly 6 env vars, read-only root filesystem | Frozen M4 spec |
| C5 | Dashboards may only query metrics the worker really exports | Frozen M6 spec |
| C6 | Every project role stays under the `iiot-workload-boundary` ceiling; new AWS calls need the role policy **and** the boundary | M5 design |
| C7 | $5/month budget; Fargate runs only while demoing (`desired_count` 0 or 1) | Cost guardrails |
| C8 | Only one worker consumes the queue at a time (detection state is per-process) | M4 design |
| C9 | Frozen specs change only with the owner's explicit approval | Project rules |

## Options

### A. Local Prometheus + Grafana, worker run on the laptop

A `monitoring` profile in `docker-compose.yml` runs Prometheus and Grafana. Grafana loads
`dashboards/*.json` automatically. Prometheus scrapes a worker started on the laptop
(`python -m iiot_monitor.worker`) that consumes the same AWS queue, with Fargate scaled to 0.

- **Trade-offs:** free and spec-safe, but the graphs show a laptop process, not the
  deployed Fargate task. The laptop worker uses your admin `aws login` session, not the
  least-privilege task role.
- **Downstream impact:** no Terraform, IAM or boundary change. The Fargate deployment stays
  monitored only by the CloudWatch dashboard and alarms (built in M4); the README must say
  so. The same Prometheus config and dashboards carry over to the S1 Kubernetes stretch goal
  (kube-prometheus-stack). For the "both GIFs" plan: **two attack runs** (one against Fargate
  for the terminal GIF and latencies, one against the laptop worker for the Grafana GIF),
  about 16 alert emails.

### B. Grafana Cloud free tier, push from a sidecar in the Fargate task

A second container in the task (Grafana Alloy) scrapes `localhost:8080/metrics` and pushes
to Grafana Cloud over HTTPS with an API token. The token lives in SSM Parameter Store
(SecureString, free tier) and is injected by ECS.

- **Trade-offs:** real Fargate metrics, dashboards viewable on a phone, no AWS metrics
  cost. Costs: a third-party account, a long-lived API token to protect and rotate, and
  free-tier terms you don't control.
- **Downstream impact:** spec-safe, because the token comes in through the **execution**
  role, not the task role (C1). It needs `ssm:GetParameters` + `kms:Decrypt` on the execution
  role **and** in the boundary (C6), so `infra/ci` must be applied from the laptop first. The
  sidecar must fit in C2's 512 MB next to the worker (**not measured yet**; see open items).
  It also needs a writable scratch volume, because the root filesystem is read-only. CI
  needs the token in GitHub secrets, or a manual one-time `put-parameter`. For "both GIFs":
  **one attack run** feeds both the terminal GIF and the Grafana GIF, about 8 emails.

### C. Amazon Managed Service for Prometheus (AMP), push from a sidecar

Same sidecar idea (AWS Distro for OpenTelemetry), pushing with SigV4 to an AMP workspace.
Grafana on the laptop queries AMP; Amazon Managed Grafana would cost about $9 per editor per
month.

- **Trade-offs:** AWS-native, no third-party token (IAM signs the requests), very low cost
  at this volume. But it breaks C1: the task role needs `aps:RemoteWrite`.
- **Downstream impact:** **a frozen-spec change (C9)** to the task-role action set, plus a
  boundary update and a new `aws_prometheus_workspace` in Terraform. The task-role
  least-privilege test then has to keep pace. Grafana still runs locally, with SigV4
  credentials. Same 512 MB and scratch-volume concerns as B. For "both GIFs": **one attack
  run**, about 8 emails.

### Rejected

| Option | Why not |
|---|---|
| Open an inbound port or add a load balancer so Prometheus can pull | Breaks C3; a load balancer bills every hour even when idle |
| ECS Exec / SSM port forwarding to the task | Adds `ssmmessages:*` to the task role (breaks C1) and opens an interactive way into the container |
| Grafana on CloudWatch metrics only (embedded metric format) | Not Prometheus queries; fails C5. CloudWatch already has its own dashboard |
| Run two workers (Fargate + laptop) at once | Breaks C8: each would see half the traffic |

## Impact comparison matrix

| Impact | A. Local | B. Grafana Cloud | C. AMP |
|---|---|---|---|
| Frozen spec change needed | ✅ None | ✅ None | ❌ Yes (task role) |
| IAM / boundary changes | ✅ None | ⚠️ Execution role + boundary | ❌ Task role + boundary |
| New Terraform resources | ✅ None | ⚠️ SSM parameter, sidecar, volume | ⚠️ AMP workspace, sidecar, volume |
| Fixed monthly cost | ✅ $0 | ✅ $0 (free tier) | ✅ ~$0 (pay per sample) |
| Graphs show the deployed Fargate worker | ❌ No (laptop) | ✅ Yes | ✅ Yes |
| New accounts or long-lived secrets | ✅ None | ❌ Account + API token | ✅ None (IAM) |
| Fits 512 MB task | ✅ Unaffected | ⚠️ Must measure | ⚠️ Must measure |
| Attack surface added | ✅ None in AWS | ⚠️ Outbound to a third party, token | ⚠️ New AWS write permission |
| Attack runs for "both GIFs" (emails) | ⚠️ 2 runs (~16) | ✅ 1 run (~8) | ✅ 1 run (~8) |
| Effort (estimate) | ✅ ~1.5 h | ⚠️ ~3 h more | ⚠️ ~3 h more |
| Reuse in S1 (Kubernetes) | ✅ Same configs | ⚠️ Partly (Alloy runs there too) | ⚠️ Partly |
| Reuse in SOC lab (Wazuh) | ⚠️ Metrics stay local | ⚠️ Metrics in third-party cloud | ✅ Metrics in the AWS account |
| Interview story | Pull vs push; why the cloud task stays closed | Push model, secret handling, SaaS trade-off | AWS-native observability, IAM-signed writes, spec change |

✅ no concern · ⚠️ manageable cost or work · ❌ breaks a constraint or adds a new class of risk

## Open items before deciding

1. **Measure the worker's memory on Fargate** (CloudWatch `AWS/ECS MemoryUtilization` from the
   2026-11-26 demo) to see how much of the 512 MB a sidecar could use. Affects B and C only.
2. For B: confirm Grafana Cloud's current free-tier limits at signup.

## Decision

**Option C: push to Amazon Managed Service for Prometheus from an ADOT collector sidecar.**

The owner rejected A because its graphs would show a laptop process instead of the deployed
worker, and preferred C over B to avoid a third-party account and a long-lived API token.
C's main drawback, the change to the frozen task-role spec (C1, C9), was approved explicitly
on 2026-11-27 and made as a separate commit (`spec(M4): allow aps:RemoteWrite ...`). The spec
stays an exact action list (7 → 8), and a new assertion pins `aps:RemoteWrite` to the
project's workspace ARN. AMP has been available in ap-south-1 since May 2023. Demo GIFs: both
(terminal + Grafana) from **one** attack run against Fargate.

## Consequences

- **Task:** a second, non-essential container (ADOT collector, pinned by version and digest)
  scrapes `localhost:8080/metrics` and remote-writes with SigV4. Its config comes from Terraform
  through the `AOT_CONFIG_CONTENT` environment variable, so there's no custom image. The worker
  stays `container_definitions[0]`, unchanged (C4).
- **IAM:** task role +`aps:RemoteWrite` on the workspace ARN; the workload boundary gets the
  same action, so `infra/ci` must be applied from the laptop **before** CI deploys this.
- **Terraform:** new `monitoring` module (AMP workspace); `compute` takes its ARN.
- **Grafana:** runs on the laptop (Docker Compose `monitoring` profile) and queries AMP with
  SigV4, using the owner's temporary session credentials. Dashboards in `dashboards/*.json`
  load automatically.
- **Memory (C2):** the sidecar shares 512 MB with the worker. If measurement shows it doesn't
  fit, raising the task to 1 GB is a **second** frozen-spec change and needs separate approval.
- **Supply chain:** ADOT v0.51.0 scanned with Trivy 0.70 on 2026-11-27: 0 HIGH/CRITICAL.

## Findings during implementation (2026-11-27, offline harness)

Tested before touching AWS: the real worker metrics, the pinned ADOT image sharing the
worker's network namespace (as in Fargate) with a read-only root filesystem, and a local
Prometheus in place of AMP. Then Grafana with the real provisioning files.

1. **Metric names survive** the OpenTelemetry round trip: `iiot_messages_total`,
   `iiot_alerts_total` and `iiot_processing_seconds_{bucket,sum,count}` keep their names and
   labels, so the dashboard's PromQL matches.
2. **Memory fits:** ADOT used ~24 MiB and the stub worker ~27 MiB, against 512 MB. No second
   spec change needed; the sidecar gets a hard cap (`collector_memory_mb`, 128). Still to
   confirm on Fargate with the real worker.
3. **Bug found and fixed: first alerts were invisible.** A Prometheus counter series appears
   already at 1, and `increase()` can't count that first step, so a rule firing once (the
   usual attack) showed 0. The worker now exports every known (rule, severity) and result at 0
   on startup (`init_metrics`); a test keeps the list in sync with `rules.py`, and a mutation
   check proved the test fails when a rule is missing.
4. **Exporter renamed:** `prometheusremotewrite` is deprecated in ADOT v0.51.0; the config uses
   `prometheus_remote_write`.
5. **Grafana 13 runs SigV4 Prometheus as a separate plugin process**
   (`grafana-amazonprometheus-datasource`), which doesn't receive host environment variables
   (the AWS credentials) unless forwarded with `GF_PLUGINS_FORWARD_HOST_ENV_VARS`. They're
   forwarded to that one plugin only.
6. **Grafana 13 downloads ~20 unpinned plugins from grafana.com on every start.** Turned off
   (`GF_PLUGINS_PREINSTALL_DISABLED`). The one plugin needed is fetched by
   `scripts/fetch-grafana-plugin.sh`, pinned to version 3.3.5 and its SHA-256, and mounted
   read-only. Grafana also checks the plugin's signature; adding a stray file to the plugin
   folder made it refuse to load the plugin (`status=modified`), so that check is live.
7. **Grafana image findings:** 13.3.1 has 20 HIGH (13.3.2: 15), all in Go libraries inside the
   binary, fixed upstream but not yet released. Accepted for a local-only tool: bound to
   `127.0.0.1`, anonymous users read-only, never deployed. Re-check on each Grafana release.
8. **Credentials:** Grafana uses the owner's temporary `aws login` session (admin). A dedicated
   read-only role (`aps:QueryMetrics`, `aps:GetLabels`, `aps:GetSeries`,
   `aps:GetMetricMetadata`) would be tighter; noted as a follow-up.

## Results in AWS (2026-11-27)

- **Memory and CPU, measured on Fargate** over ~3.5 hours with both containers: memory 15.1%
  average, 15.2% peak of 512 MB (the worker alone peaked at 10.4% the day before, so the
  sidecar adds ~5 points, about 25 MB, close to the ~24 MiB measured offline). CPU 9.0% average,
  27.2% peak of 0.25 vCPU. No memory spec change needed; open item 1 is closed.
- **End to end:** the sidecar pushed every 15 s with no errors in its log; all 8 alert series of
  the attack run were visible in Grafana through AMP.
- **Credentials (finding 8, solved):** `aws login` credentials rotate about every 15 minutes, so
  a one-off copy made Grafana go blank mid-recording twice. Grafana now uses
  `credential_process = cat /aws/creds.json`, and `scripts/grafana-aws-creds.sh` rewrites that file
  from the live session every minute. The SDK re-runs the process when its credentials expire.
  Verified: one Grafana container served queries for 2+ hours across ~9 rotations, no restart.
- **Silent auto-refresh failure:** the dashboard asked for a 15 s refresh, which isn't in
  Grafana's allowed list, so Grafana silently disabled auto-refresh. Backend checks all passed;
  only the browser recording showed a frozen view. Now 10 s, with the allowed intervals stated.
- **Counts shown to people are rounded:** `increase()` extrapolates to the window edges, so a
  counter stepping 0 → 1 reads ~1.3. The count panels round each series to whole numbers.
