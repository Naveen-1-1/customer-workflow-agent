"""`make load`: start the fake model server and the app, run Locust headless, then stop both.

Settings (environment variables):
    USERS=20 SPAWN_RATE=2 DURATION=2m   Locust: simulated customers, how fast they arrive, how long
    RPM=36                              the app's LLM requests-per-minute limit (6000 = no limit)
    LATENCY_S=0.5 JITTER_S=1.0          how slow the fake models answer
    FAULTS='{"rate_limit": 0.2}'        extra fake-model faults for every model (see fake_llm.py)
    MAX_OPEN_CHATS=10                   the app's capacity (any app setting can be passed)

The app runs on :8010 with its own data in var/load/ (wiped first), so the demo data in var/ is
untouched. Tracing is forced off. Results: var/load/report.html and var/load/stats_*.csv.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
VENV = Path(sys.executable).parent
OUT = ROOT / "var" / "load"
FAKE, APP = "http://127.0.0.1:8100", "http://127.0.0.1:8010"


def wait_ready(url: str, seconds: float = 30) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise SystemExit(f"{url} didn't come up in {seconds:.0f}s")


def summary() -> None:
    metrics = httpx.get(f"{APP}/metrics", timeout=5).text
    wanted = ("agent_chats_refused_total ", "llm_requests_total{", "agent_reply_seconds_count{")
    totals: dict[str, float] = {}
    for line in metrics.splitlines():
        if line.startswith(wanted):
            name, value = line.rsplit(" ", 1)
            key = name.split("{")[0] + (
                "{" + name.split("served_by=")[1].split(",")[0].rstrip("}") + "}"
                if "served_by=" in name
                else ""
            )
            totals[key] = totals.get(key, 0) + float(value)
    print("\n== App ==")
    for key, value in sorted(totals.items()):
        print(f"  {key}: {value:.0f}")
    print("== Fake models ==")
    for model, results in httpx.get(f"{FAKE}/_stats", timeout=5).json().items():
        print(f"  {model}: {results}")
    print(f"Report: {(OUT / 'report.html').relative_to(ROOT)}")


def main() -> int:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    env = {
        **os.environ,
        "NVIDIA_API_KEY": "fake-key",  # never the real key: everything goes to the fake server
        "LLM_BASE_URL": f"{FAKE}/v1",
        "LLM_REQUESTS_PER_MINUTE": os.environ.get("RPM", "36"),
        "VAR_DIR": str(OUT / "var"),
        "LANGSMITH_TRACING": "false",
    }
    procs = [
        subprocess.Popen(
            [
                VENV / "uvicorn",
                "customer_workflow_agent.devtools.fake_llm:app",
                "--port",
                "8100",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
        ),
        subprocess.Popen(
            [
                VENV / "uvicorn",
                "--factory",
                "customer_workflow_agent.api.app:create_app",
                "--port",
                "8010",
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
        ),
    ]
    try:
        wait_ready(f"{FAKE}/_stats")
        wait_ready(f"{APP}/readyz")
        faults = {
            "latency_s": float(os.environ.get("LATENCY_S", "0.5")),
            "jitter_s": float(os.environ.get("JITTER_S", "1.0")),
            **json.loads(os.environ.get("FAULTS") or "{}"),
        }
        httpx.post(f"{FAKE}/_faults", json={"model": "*", **faults}, timeout=5).raise_for_status()
        print(f"Fake model faults: {faults}")
        code = subprocess.call(
            [
                VENV / "locust",
                "-f",
                ROOT / "load" / "locustfile.py",
                "--headless",
                "--host",
                APP,
                "-u",
                os.environ.get("USERS", "20"),
                "-r",
                os.environ.get("SPAWN_RATE", "2"),
                "-t",
                os.environ.get("DURATION", "2m"),
                "--html",
                OUT / "report.html",
                "--csv",
                OUT / "stats",
                "--only-summary",
            ],
            cwd=ROOT,
        )
        summary()
        return code
    finally:
        # The app first, and wait for it: it may still be finishing queued work, which needs the
        # fake models.
        for p in reversed(procs):
            p.send_signal(signal.SIGINT)
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()


if __name__ == "__main__":
    sys.exit(main())
