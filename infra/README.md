# Infrastructure (M2): AWS IoT Core, SQS, S3 via Terraform

Region: **ap-south-1** (pinned in every provider block). AWS CLI profile: `iiot`.

| Stack | What it holds | State |
|---|---|---|
| `bootstrap/` | S3 bucket for Terraform remote state (versioned, encrypted, TLS-only) | local `terraform.tfstate` (back it up, see below) |
| `envs/dev/` | IoT Things, CSR-signed certs, device policy, IoT logging, topic rule → SQS (+DLQ) and S3 raw archive | S3 bucket, native lockfile |

Device private keys are generated locally (`scripts/gen-aws-device-keys.sh`); only CSRs
(committed in `envs/dev/device-csrs/`) go to AWS, so keys never enter Terraform state.

## First-time deploy (new account)
```bash
cd infra/bootstrap
AWS_PROFILE=iiot terraform init && AWS_PROFILE=iiot terraform apply
cd ../envs/dev
cp backend.hcl.example backend.hcl          # fill in the bootstrap output state_bucket
cd ../../..
./scripts/gen-aws-device-keys.sh
cd infra/envs/dev
AWS_PROFILE=iiot terraform init -backend-config=backend.hcl
AWS_PROFILE=iiot terraform apply           # review the plan before typing yes
```

### Back up the bootstrap state
It lives only at `infra/bootstrap/terraform.tfstate`
(Windows Explorer: `\\wsl.localhost\Ubuntu-24.04\home\purvankit\projects\iiot-secure-monitor\infra\bootstrap`).
Copy it into the versioned state bucket after any bootstrap change:
```bash
aws s3 cp infra/bootstrap/terraform.tfstate \
  s3://iiot-secure-monitor-tfstate-<ACCOUNT_ID>/bootstrap/terraform.tfstate \
  --profile iiot --region ap-south-1
```

## Run the simulator against AWS (from scratch)
Every step starts from the repo root. If unsure where you are: `cd ~/projects/iiot-secure-monitor`.

**1. Python env** (first time only; skip if `.venv/` exists)
```bash
python3 -m venv .venv
.venv/bin/pip install -e './simulator[dev]'
```

**2. Sign in** (whenever credentials have expired)
```bash
aws login --profile iiot
aws sts get-caller-identity --profile iiot
```
Check the account is the dedicated iiot account. If not, **stop**.

**3. Check the infrastructure exists**
```bash
cd ~/projects/iiot-secure-monitor
ls infra/envs/dev/backend.hcl || cp infra/envs/dev/backend.hcl.example infra/envs/dev/backend.hcl
# (if just copied, fill in the account ID)
AWS_PROFILE=iiot terraform -chdir=infra/envs/dev init -backend-config=backend.hcl
AWS_PROFILE=iiot terraform -chdir=infra/envs/dev plan
```
Expect `No changes`. If it plans 36 creates (stack was destroyed), run
`AWS_PROFILE=iiot terraform -chdir=infra/envs/dev apply` and review before typing `yes`.

**4. Device keys and certificates**
```bash
cd ~/projects/iiot-secure-monitor
./scripts/gen-aws-device-keys.sh               # prints "skip ..." if keys exist
AWS_PROFILE=iiot ./scripts/fetch-aws-device-certs.sh
ls local/aws-certs/
```
Expect `ca.crt` plus a `.crt` and `.key` per device (5 devices).
If step 4 prints `key+csr` instead of `skip` (new machine / deleted keys), the CSRs
changed: re-run step 3's `apply` so AWS issues new certs, then fetch again.

**5. Start three processes, one per terminal.** Paste this at the top of each:
```bash
cd ~/projects/iiot-secure-monitor/simulator
export MQTT_HOST=$(terraform -chdir=../infra/envs/dev output -raw iot_endpoint)
export CERTS_DIR=../local/aws-certs INTERVAL_S=5
```
- Terminal 1, PLC: `../.venv/bin/python -m iiot_sim.plc_sim --host 127.0.0.1`
- Terminal 2, gateway: `../.venv/bin/python -m iiot_sim.gateway`
- Terminal 3, devices: `../.venv/bin/python -m iiot_sim.run_devices`

Expect `connected to ...amazonaws.com:8883`, then a reading per device about every 5 s.
Run on the host, not Docker: keys are owner-only (`600`), so the container's non-root
user can't read them (intentional).

**6. Check data arrives** (4th terminal; short lines, so pasting can't split them)
```bash
cd ~/projects/iiot-secure-monitor
export AWS_PROFILE=iiot AWS_REGION=ap-south-1
Q=$(terraform -chdir=infra/envs/dev output -raw telemetry_queue_url)
B=$(terraform -chdir=infra/envs/dev output -raw raw_bucket)
echo "$Q  $B"    # blank means you're not in the repo root
aws sqs get-queue-attributes --queue-url "$Q" --attribute-names ApproximateNumberOfMessages
aws s3 ls "s3://$B/raw/" --recursive | tail -5
```
The queue count rises about 1 per second; new `.json` objects appear per device.
Console alternative: **IoT Core → MQTT test client**, subscribe to `iiot/#`.

**7. Security tests (optional).** Stop terminal 3 first: the tests connect as feeder-01.
```bash
cd ~/projects/iiot-secure-monitor/simulator
IOT_ENDPOINT=$MQTT_HOST ../.venv/bin/pytest -m aws -v
```
Expect 5 passed.

**8. Stop.** Ctrl+C in terminals 1-3. Nothing in M2 bills hourly, so cost stops with the
simulator. Remove everything: `AWS_PROFILE=iiot terraform -chdir=infra/envs/dev destroy`.

## Deploy through CI/CD (M5, the normal way)

```
PR opened  -> ci.yml: ruff, pytest (3 suites + finished specs), terraform fmt/validate/test,
              docker build + Trivy (fails on HIGH/CRITICAL)
           -> deploy.yml "plan": terraform plan with the read-only role (summary on the run page)
merge      -> deploy.yml "deploy": waits for YOUR approval (environment `dev`), then
              build -> Trivy -> push image tagged with the commit SHA -> terraform apply (0 tasks)
```
`main` is protected: it only changes through a PR whose checks passed, so every deploy
ships tested code. Approve on GitHub: the run page shows **Review deployments** (works in
the phone app too).

**Demo (start/stop the worker):** GitHub → Actions → **deploy** → **Run workflow** →
`desired_count` = `1` → approve. Stop the same way with `0`. Fargate bills per hour, and the
next merge resets it to 0 anyway.

**Rules now:**
- Don't `terraform apply` `infra/envs/dev` from the laptop any more (CI owns it; a laptop
  apply without `-var image_tag=<deployed sha>` would point the task at a missing image).
  Read-only `plan` locally is fine.
- `infra/ci` (OIDC provider, CI roles, permissions boundary) is the exception: only the
  owner applies it, from the laptop. CI must never be able to change its own permissions.
- A new AWS call in the worker needs: the task-role policy (`modules/compute`), AND the
  workload boundary (`modules/ci_oidc`, applied from the laptop first). Otherwise AccessDenied.
- A HIGH/CRITICAL CVE with no fix yet will turn CI red. Don't loosen the gate: if the risk is
  acceptable, create `.trivyignore.yaml` with the CVE `id`, a `statement` (why) and
  `expired_at`, and add `trivyignores: .trivyignore.yaml` to both Trivy steps, in a PR, so
  the decision is reviewed and expires. (Not needed yet: 0 HIGH/CRITICAL on 2026-11-26.)

**One-time setup (done 2026-11-26; repeat only for a new account or repo):**
```bash
cd ~/projects/iiot-secure-monitor
export AWS_PROFILE=iiot AWS_REGION=ap-south-1
terraform -chdir=infra/ci init -backend-config=../envs/dev/backend.hcl
terraform -chdir=infra/ci apply
```
Then in GitHub: environment `dev` (required reviewer = you, deployment branches = `main`),
repository secrets `AWS_PLAN_ROLE_ARN`, `TF_STATE_BUCKET`, `ALERT_EMAIL`, and the `dev`
environment secret `AWS_APPLY_ROLE_ARN` (values from `terraform -chdir=infra/ci output`,
`backend.hcl` and `terraform.tfvars`). Secrets keep the account ID out of the public repo;
the workflows also mask it in logs.

## Grafana dashboards (M6)

The Fargate task accepts no inbound connections, so its ADOT sidecar **pushes** the worker's
metrics to Amazon Managed Prometheus (ADR `docs/adr/0001-grafana-metrics-source.md`). Grafana
runs on the laptop and reads them from there. The worker must be running (Actions → deploy →
Run workflow → `desired_count` 1) for new data to appear.

**1. One-time: fetch the pinned Grafana plugin** (checksum-verified):
```bash
cd ~/projects/iiot-secure-monitor
./scripts/fetch-grafana-plugin.sh
```

**2. Start Grafana** with temporary AWS credentials (re-run all four lines when the session expires):
```bash
cd ~/projects/iiot-secure-monitor
export AMP_QUERY_URL=$(AWS_PROFILE=iiot terraform -chdir=infra/envs/dev output -raw prometheus_query_url)
eval "$(aws configure export-credentials --profile iiot --format env)"
docker compose --profile monitoring up -d --force-recreate grafana
```
Open http://localhost:3000. The "IIoT detection worker" dashboard is the home page.

**3. Stop it:**
```bash
cd ~/projects/iiot-secure-monitor
docker compose stop grafana
```

If panels show "No data": check the worker is running, then the sidecar's log in CloudWatch
(`/ecs/iiot-worker`, streams `metrics-forwarder/...`).

## Run the detection service on Fargate by hand (M4; break-glass only since M5)
Fargate bills per task-hour (~$0.012/h at the indicative us-east-1 rate in the project brief, plus $0.005/h for the public IPv4), so the service
sits at `desired_count = 0` and only runs while you demo. Every step starts from the repo root.

**1. Build and push the image.** Tags are immutable: one tag per build, never `latest`.
```bash
cd ~/projects/iiot-secure-monitor
export AWS_PROFILE=iiot AWS_REGION=ap-south-1
REPO=$(terraform -chdir=infra/envs/dev output -raw ecr_repository_url)
TAG=$(git rev-parse --short HEAD)
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker build --provenance=false -t $REPO:$TAG service
docker push $REPO:$TAG
```
`--provenance=false` matters: without it BuildKit pushes an OCI *index* (image + attestation),
which ECR basic scanning can't scan (`UnsupportedImageTypeException`), and scan-on-push
silently does nothing. If that tag already holds a different image, `docker push` fails ("tag invalid"):
that is immutability working; build from a new commit.

**2. Check the scan** (findings are in the base image's Debian packages, not our code):
```bash
aws ecr describe-image-scan-findings --repository-name iiot-monitor --image-id imageTag=$TAG --query imageScanFindings.findingSeverityCounts
```

**3. Start one worker** (review the plan before typing `yes`):
```bash
terraform -chdir=infra/envs/dev apply -var image_tag=$TAG -var desired_count=1
aws ecs describe-services --cluster iiot --services iiot-worker --query 'services[0].deployments[0].[rolloutState,runningCount]'
aws logs tail /ecs/iiot-worker --follow
```
Expect `COMPLETED 1` after ~2-4 min and a `registry: [...]` log line. Then run the simulator
(previous section, step 5): readings land in DynamoDB with no laptop credentials involved.

**4. Stop it** (same tag, so only the count changes):
```bash
terraform -chdir=infra/envs/dev apply -var image_tag=$TAG -var desired_count=0
```

**Alarms** (all email the `iiot-alerts` topic): `iiot-worker-processing-errors` (a
`processing failed` log line), `iiot-worker-dlq-not-empty` (a message failed 5 times),
`iiot-worker-not-running` (exists only while `desired_count = 1`). Dashboard: CloudWatch →
Dashboards → `iiot-secure-monitor`.

## Known limitation
Clients with no valid certificate fail during the TLS handshake, before IoT Core sees an
MQTT client ID, so these attempts are blocked but not logged or attributable to a device.
Prevention covers them; detection (M3, rule 1) covers authenticated misuse: wrong client
ID, policy violations, and payload `device_id` not matching the broker-verified client ID.
