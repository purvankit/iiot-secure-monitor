# Amazon Managed Service for Prometheus (AMP) workspace: long-term store for the worker's
# Prometheus metrics (ADR 0001). The Fargate task can't be scraped from outside (no inbound
# rules), so an ADOT collector sidecar scrapes it over localhost and remote-writes here.
# Grafana (on the laptop) queries this workspace with SigV4-signed requests.
#
# Cost: no fixed fee. Billed per sample ingested, per GB stored and per sample queried;
# at ~15 s scrapes of a few dozen series while demoing, that's cents per month.

terraform {
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

resource "aws_prometheus_workspace" "metrics" {
  alias = "iiot-metrics"
}
