"""Print alerts as the detection worker stores them (the "cloud side" of the attack demo).

Polls the alerts table and prints each new alert once, with its detection latency:
raised_at (worker clock) - detected_at (IoT Core receive time of the triggering message).
Both are AWS clocks (Fargate uses Amazon Time Sync), so the laptop's clock skew doesn't
matter. For auth-failure alerts, detected_at is the IoT Core log event time.

Usage (repo root), next to `attacks/run_attack.py all` in another terminal:
  AWS_PROFILE=iiot AWS_REGION=ap-south-1 .venv/bin/python attacks/watch_alerts.py
Stop with Ctrl+C.
"""

from __future__ import annotations

import argparse
import os
import time

import boto3
from boto3.dynamodb.conditions import Attr
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

COLOR = {"critical": "\033[1;31m", "high": "\033[31m", "medium": "\033[33m"}
RESET = "\033[0m"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--table", default=os.getenv("TABLE_ALERTS", "iiot-alerts"))
    p.add_argument("--since-s", type=int, default=0, help="also show alerts from the last N s")
    p.add_argument("--interval-s", type=float, default=1.0)
    a = p.parse_args()

    region = os.environ["AWS_REGION"]  # explicit, like the worker: never a profile default
    # Standard retry mode retries throttling and connection errors (e.g. a brief DNS failure)
    # with backoff instead of crashing the demo.
    cfg = Config(retries={"mode": "standard", "max_attempts": 10})
    table = boto3.resource("dynamodb", region_name=region, config=cfg).Table(a.table)
    since = int(time.time() * 1000) - a.since_s * 1000
    seen: set[str] = set()
    print(f"[cloud] watching {a.table} for new alerts ...", flush=True)
    while True:
        items, kw = [], {"FilterExpression": Attr("raised_at").gte(since)}
        try:
            while True:
                page = table.scan(**kw)
                items += page["Items"]
                if "LastEvaluatedKey" not in page:
                    break
                kw["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        except (BotoCoreError, ClientError) as e:
            # Print the error type only: endpoint hostnames can contain the account ID.
            print(f"[cloud] (poll failed: {type(e).__name__}; retrying)", flush=True)
            time.sleep(2)
            continue
        for it in sorted(items, key=lambda i: int(i["raised_at"])):
            if it["alert_id"] in seen:
                continue
            seen.add(it["alert_id"])
            lat = (int(it["raised_at"]) - int(it["detected_at"])) / 1000
            sev = str(it["severity"])
            print(f"[cloud] {COLOR.get(sev, '')}ALERT {sev:<8}{RESET} {it['rule']:<28} "
                  f"device={it['device_id']:<11} detected in {lat:.2f} s", flush=True)
        time.sleep(a.interval_s)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
