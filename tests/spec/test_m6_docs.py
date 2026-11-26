"""M6 SPEC (written before the code, 2026-11-26): dashboards and README.

Frozen: changing an assertion needs the owner's approval.
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_SRC = (ROOT / "service/iiot_monitor/worker.py").read_text()


def exported_metrics() -> set[str]:
    """Metric names the service really exports (parsed from its Counter/Histogram defs)."""
    names = set()
    for kind, name in re.findall(r'(Counter|Histogram|Gauge)\(\s*"(\w+)"', SERVICE_SRC):
        base = name.removesuffix("_total")
        names |= {base, f"{base}_total"} if kind == "Counter" else {name}
        if kind == "Histogram":
            names |= {f"{name}_bucket", f"{name}_sum", f"{name}_count"}
    return names


def grafana_dashboards():
    files = sorted((ROOT / "dashboards").glob("*.json"))
    assert files, "dashboards/*.json (Grafana) does not exist yet"
    return [(f, json.loads(f.read_text())) for f in files]


def exprs(dashboard):
    for panel in dashboard.get("panels", []):
        for t in panel.get("targets", []):
            if t.get("expr"):
                yield panel.get("title", "?"), t["expr"]


def test_dashboards_only_query_metrics_the_service_exports():
    known = exported_metrics()
    assert {"iiot_messages_total", "iiot_alerts_total"} <= known
    for f, d in grafana_dashboards():
        found = list(exprs(d))
        assert found, f"{f.name} has no Prometheus queries"
        for title, e in found:
            for metric in re.findall(r"\biiot_\w+", e):
                assert metric in known, f"{f.name} panel '{title}': {metric} is not exported"


def test_dashboard_covers_traffic_alerts_and_latency():
    queried = {m for _, d in grafana_dashboards() for _, e in exprs(d)
               for m in re.findall(r"\biiot_\w+", e)}
    assert any(m.startswith("iiot_messages") for m in queried)
    assert any(m.startswith("iiot_alerts") for m in queried)
    assert any(m.startswith("iiot_processing_seconds") for m in queried)


README_SECTIONS = ["architecture", "threat model", "iec 62443", "cost", "demo", "limitation"]


def test_readme_has_required_sections():
    text = (ROOT / "README.md").read_text().lower()
    headings = " ".join(re.findall(r"^#+\s*(.+)$", text, re.M))
    missing = [s for s in README_SECTIONS if s not in headings]
    assert not missing, f"README.md is missing sections: {missing}"


def test_readme_images_exist():
    text = (ROOT / "README.md").read_text()
    imgs = re.findall(r"!\[[^\]]*\]\(([^)\s]+)\)", text)
    assert any(i.endswith(".gif") for i in imgs), "README needs the demo GIF"
    for i in imgs:
        if not i.startswith("http"):
            assert (ROOT / i).is_file(), f"README references missing image {i}"


def test_readme_has_iec_62443_mapping_table():
    text = (ROOT / "README.md").read_text()
    sec = text.lower().split("62443", 1)[-1]
    assert re.search(r"^\|.*\|\s*$", sec, re.M), "IEC 62443 mapping must be a table"
    assert re.search(r"\bFR ?[1-7]\b|\bSR ?\d", text), "map to 62443 FRs/SRs by number"


def test_readme_makes_no_unmeasured_accuracy_claims():
    # CLAUDE.md: only numbers actually measured. A blanket "100%"/"accuracy" claim from a
    # one-run demo is exactly what not to write.
    text = (ROOT / "README.md").read_text().lower()
    assert "100%" not in text and "accuracy" not in text
