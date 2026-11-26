# CLAUDE.md: Secure Industrial IoT Monitoring Platform on AWS

> Project instructions for Claude Code. Personal context (background, job-search goals, resume) lives in `~/.claude/CLAUDE.md`, outside this repo.
>
> **Current state, next steps and lessons learned: read `PROGRESS.md` first** and update it at the end of each session.

## Working rules
- Explain the *why* behind every design decision; the owner must be able to defend it in interviews.
- Never invent metrics or features in docs/README; only document what is built and measured.
- Keep scope to v1 below; propose additions only with a clear reason.
- **Spec tests come first and are frozen.** `tests/spec/` holds each milestone's tests, written before the code (2026-11-26). A milestone is done only when its spec is green. Never weaken, skip or delete a spec assertion without the owner's explicit approval; adding assertions is fine. Every change to `tests/spec/` must be called out to the owner (audit: `git log -p tests/spec`).
- Cost safety first: no NAT gateways, no EKS, no idle load balancers; tag everything `project=iiot-secure-monitor`.
- AWS: use the dedicated account/project and CLI profile for this project only. Never deploy into the existing "building things" account or project.
- AWS CLI profile is `iiot` (credentials via `aws login`). The profile's region is us-east-1 only because `aws login` failed against the ap-south-1 sign-in endpoint. Every project resource goes in **ap-south-1**: pin `region = "ap-south-1"` in the Terraform provider and pass `--region ap-south-1` (or set `AWS_REGION=ap-south-1`) on CLI/SDK calls. Never rely on the profile default.

---

## 3. Problem statement

Industrial sites (substations, factories) are connecting OT devices to the cloud for monitoring. That creates two needs: **reliable telemetry** and **visibility into attacks**, such as rogue devices, spoofed sensor values, message floods and replayed commands. That last one is exactly the class of attack my GOOSE research studied.

**Build:** a cloud platform that securely ingests telemetry from simulated industrial devices, stores and visualises it, and **detects and alerts on suspicious behaviour**. The entire platform is defined in Terraform and shipped through a CI/CD pipeline.

---

## 4. Architecture (v1)

```
[Simulated devices (Python)]          [Simulated PLC: Modbus TCP]
  feeder bay / motor / transformer           |
  MQTT over TLS + X.509 per device     [Edge gateway (Python, pymodbus)]
            \                                /  MQTT over TLS
             \______________________________/
                            |
                 AWS IoT Core (Mumbai, ap-south-1)
           - Thing registry, one certificate per device
           - Least-privilege IoT policies (device may only publish to its own topic)
           - Lifecycle and auth-failure events -> CloudWatch Logs
                            |
                     IoT Rules Engine
              /                             \
     SQS queue (buffer)                 S3 (raw archive, lifecycle policy)
             |
  Ingest + Detection service (Python, Docker) on ECS Fargate
     - validates and normalises readings
     - detection rules (below)
     - exposes /metrics for Prometheus, /health
             |                   \
   DynamoDB                       SNS -> email alerts
   (latest state, readings,
    alerts table)
             |
   Grafana dashboards + CloudWatch dashboard and alarms
```

### Detection rules (v1)
1. **Unknown or unauthorised device:** connect or auth failures, or messages from a client ID not in the registry.
2. **Physically implausible values:** out of range, or a rate of change that's impossible (for example frequency jumping 5 Hz in 100 ms).
3. **Message flood / DoS:** per-device message rate above a threshold.
4. **Replay and sequence anomalies:** a monotonic sequence number and timestamp per device, with duplicate, regressed or stale messages flagged (inspired by GOOSE stNum/sqNum).
5. **Breaker state change without a matching command** (stretch): an OT-logic anomaly.

Each alert is stored with device, rule, evidence and severity, and an SNS notification is sent.

### Key design choices (be ready to explain in interviews)
- **IoT Core instead of self-hosted Mosquitto in the cloud:** managed mutual TLS, per-device identity and policy-based authorisation.
- **SQS between IoT Core and the service:** absorbs bursts, allows retries and a dead-letter queue, and keeps the components decoupled.
- **Fargate instead of EC2:** no servers to patch. The cost is controlled by scaling `desired_count` to 0 when not demoing.
- **No NAT gateway:** the task runs in a public subnet with a public IP, no inbound rules and egress only, because NAT gateways bill hourly. (Mention VPC endpoints as the production alternative.)
- **GitHub Actions authenticates to AWS with OIDC:** no long-lived access keys stored in GitHub.
- **Everything in Terraform:** remote state in S3 with locking, and `terraform plan` on every pull request.

### Out of scope for v1 (resist scope creep)
- Machine learning anomaly detection (rule-based is enough and easier to explain)
- A custom frontend (Grafana is the UI)
- Multiple regions or high availability
- Managed Kubernetes on AWS (EKS costs money even when idle). Kubernetes is a local stretch goal.

---

## 5. Repo layout (proposed)

```
iiot-secure-monitor/
├── README.md               # problem, architecture diagram, threat model, demo GIF, cost notes
├── CLAUDE.md               # this brief
├── simulator/              # device simulator + Modbus PLC sim + edge gateway
├── service/                # ingest + detection service (Python), Dockerfile
│   └── tests/              # pytest: detection rules, parsing, idempotency
├── infra/                  # Terraform
│   ├── bootstrap/          # state bucket, lock
│   ├── modules/            # iot, queue, compute, data, monitoring, ci_oidc
│   └── envs/dev/
├── dashboards/             # Grafana JSON, CloudWatch dashboard
├── attacks/                # demo scripts: rogue device, spoof, flood, replay
├── k8s/                    # stretch: manifests/Helm for local k3d/kind
└── .github/workflows/      # ci.yml (lint, test, scan, build), deploy.yml (plan/apply)
```

---

## 6. Milestones (about 45 h core, plus a 10–15 h stretch)

| # | Milestone | Est. | Done when |
|---|---|---|---|
| M0 | AWS safety setup: separate project, MFA on root, IAM user/SSO, **$5 budget alert**, CLI, Terraform, WSL2 + Docker, repo created | 2 h | Budget alert email received; `terraform version` and `aws sts get-caller-identity` work |
| M1 | Device simulator + Modbus PLC sim + edge gateway, tested locally against Mosquitto (docker-compose) | 8 h | 5 devices publishing; pytest passes |
| M2 | Terraform: IoT Core things, certificates, policies, rules → SQS + S3; simulator connects to AWS | 8 h | Messages visible in SQS and S3; a device can't publish to another device's topic |
| M3 | Ingest + detection service + DynamoDB + SNS; unit tests for every rule | 8 h | Each rule has a passing test; alerts land in DynamoDB and email |
| M4 | Containerise; ECR + ECS Fargate via Terraform; CloudWatch logs, dashboard, 2–3 alarms | 8 h | Service runs in AWS; alarm fires on a forced error |
| M5 | GitHub Actions: ruff + pytest + Trivy image scan + build/push to ECR (OIDC) + terraform fmt/validate/plan on PR, apply on main (manual approval) | 6 h | Green pipeline; a merge deploys automatically |
| M6 | Grafana dashboards, attack demo scripts, README (architecture diagram, threat model, IEC 62443 mapping table, cost notes, demo GIF) | 5 h | Someone can understand and reproduce the project from the README |
| S1 | Stretch: run the same containers on local Kubernetes (k3d/kind) with Helm, plus kube-prometheus-stack | 10–15 h | `kubectl get pods` healthy; Grafana shows service metrics |

After this, the **SOC home lab** project ingests this platform's alerts and AWS logs into Wazuh.

---

## 7. Cost guardrails

- **Budget alert at $5/month before creating anything.** Tag every resource `project=iiot-secure-monitor`.
- AWS Free Tier for accounts created since 15 Jul 2025 is credit-based ($100, plus up to $100 more for activities, Free plan lasts up to 6 months). I already have an AWS account, so I'm probably **not eligible** for new-account credits. Verify on the Billing → Credits page.
- **Fargate has no free tier.** Indicative us-east-1 rate: ~$0.0405 per vCPU-hour and ~$0.0044 per GB-hour. A 0.25 vCPU / 0.5 GB task running 24/7 is ≈ $9/month, so **set `desired_count = 0` when not demoing**. Mumbai rates differ slightly; check the AWS Pricing Calculator.
- **IoT Core:** ~$1 per million messages plus negligible connection cost. Publish every 5–10 s and only run the simulator while working.
- **Avoid:** NAT gateways, EKS, idle load balancers, CloudWatch log retention set to "never expire" (set 7–14 days). App Runner is closed to new customers, so don't plan around it.
- `terraform destroy` the dev environment at the end of a work week if credits are tight.

---
