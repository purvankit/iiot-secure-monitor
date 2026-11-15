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

## Known limitation
Clients with no valid certificate fail during the TLS handshake, before IoT Core sees an
MQTT client ID, so these attempts are blocked but not logged or attributable to a device.
Prevention covers them; detection (M3, rule 1) covers authenticated misuse: wrong client
ID, policy violations, and payload `device_id` not matching the broker-verified client ID.
