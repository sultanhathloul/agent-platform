# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx>=0.27"]
# ///
"""Sandbox memory measurements (timeout budget PR, I7; ADR-0252).

child  — inside the agent image (the ingest service's), one sandbox child per file of the phase 04
         corpus (evals/docs), under the configured limits: its peak resident memory (VmHWM) and its
         peak address space (VmPeak, what RLIMIT_AS limits), in MiB.

    docker compose run --rm --no-deps -e PYTHONPATH=/app -v "$PWD/evals/docs":/corpus:ro \\
        -v "$PWD/scripts":/scripts:ro \\
        ingest python /scripts/measure_sandbox.py child /corpus

parent — on the host, against the running stack: the peak resident memory of a document service's
         own process (pid 1 of ingest or mcp-rag-ref, sandbox children excluded) while two users'
         files are parsed at once, `rounds` times. The peak is reset before each round
         (/proc/1/clear_refs). For mcp-rag-ref run it with the agent on DOCS_BACKEND=mcp.

    uv run scripts/measure_sandbox.py parent ingest 3
"""

import json
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FILES = ("twenty-pages.pdf", "leave-regulation.pdf")


def _status_mib(field: str) -> float:
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(field + ":"):
            return int(line.split()[1]) / 1024
    return 0.0


def _measured_child(conn, kind, data, config) -> None:
    """The sandbox child (app/rag/ingest/sandbox.py `_child`) with its limits, reporting its peaks."""
    import resource

    from app.rag.ingest import sandbox

    limits = config.ingest.sandbox
    resource.setrlimit(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds + 1))
    memory = limits.memory_mb * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    message = sandbox.outcome(kind, data, config)
    conn.send((message[0], _status_mib("VmHWM"), _status_mib("VmPeak")))
    conn.close()


def child(corpus: str) -> int:
    import multiprocessing

    from app.rag.config import load_docs_config
    from app.rag.extract import detect_kind
    from app.settings import Settings

    config = load_docs_config(Settings().config_dir)
    context = multiprocessing.get_context("spawn")
    worst_rss = worst_vm = 0.0
    for path in sorted(Path(corpus).iterdir()):
        data = path.read_bytes()
        kind = detect_kind(path.name, data, config)
        if kind is None:
            continue
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=_measured_child, args=(send, kind, data, config))
        process.start()
        send.close()
        status, rss, vm = receive.recv() if receive.poll(180) else ("lost", 0.0, 0.0)
        process.join(10)
        worst_rss, worst_vm = max(worst_rss, rss), max(worst_vm, vm)
        print(f"{path.name:28s} {status:6s} peak RSS {rss:7.1f} MiB  peak address space {vm:7.1f} MiB")
    print(f"WORST child: peak RSS {worst_rss:.1f} MiB, peak address space {worst_vm:.1f} MiB "
          f"(limit {config.ingest.sandbox.memory_mb} MiB, RLIMIT_AS)")
    return 0


def _exec(service: str, command: str) -> str:
    return subprocess.run(
        ["docker", "compose", "exec", "-T", service, "sh", "-c", command],
        cwd=REPO, check=True, capture_output=True, text=True,
    ).stdout


def parent(service: str, rounds: int) -> int:
    import httpx

    sys.path.insert(0, str(REPO / "scripts"))
    from get_token import env, login

    cfg = env()
    gateway = cfg["GATEWAY_PUBLIC_URL"]
    users = ("analyst", "evaluator")
    peaks = []
    with httpx.Client(timeout=120) as http:
        for _ in range(rounds):
            tokens = {u: login(u, u)["access_token"] for u in users}
            _exec(service, "echo 5 > /proc/1/clear_refs")

            def upload(pair):
                user, name = pair
                headers = {"Authorization": f"Bearer {tokens[user]}"}
                conversation = str(uuid.uuid4())
                with (REPO / "evals/docs" / name).open("rb") as handle:
                    response = http.post(f"{gateway}/v1/conversations/{conversation}/attachments",
                                         files={"file": (name, handle)}, headers=headers)
                response.raise_for_status()
                attachment = response.json()["data"]["id"]
                for _ in range(240):
                    read = http.get(f"{gateway}/v1/conversations/{conversation}/attachments/{attachment}",
                                    headers=headers)
                    status = read.json()["data"]["status"]
                    if status in ("indexed", "failed"):
                        return status
                    time.sleep(0.5)
                return "pending"

            with ThreadPoolExecutor(2) as pool:
                statuses = list(pool.map(upload, zip(users, FILES)))
            hwm = next(int(line.split()[1]) for line in _exec(service, "cat /proc/1/status").splitlines()
                       if line.startswith("VmHWM:")) / 1024
            peaks.append(hwm)
            print(f"{service}: two files at once {statuses}, parent peak RSS {hwm:.1f} MiB")
    print(json.dumps({"service": service, "parent_peak_mib": max(peaks), "rounds": rounds}))
    return 0


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "child":
        sys.exit(child(sys.argv[2]))
    sys.exit(parent(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3))
