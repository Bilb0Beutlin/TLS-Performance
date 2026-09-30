#!/usr/bin/env python3
import csv
import re
import subprocess
import threading
import time
from pathlib import Path

# ============================================================
# Hardcoded configuration
# ============================================================

CIPHER_LABEL = "TLSv13-TLS_CHACHA20_POLY1305_SHA256"
TARGET_URL = "https://localhost:443/data"

H2LOAD_BIN = "h2load"
H2LOAD_DURATION_SECONDS = 10
H2LOAD_CLIENTS = 20
H2LOAD_THREADS = 1
H2LOAD_MAX_CONCURRENT_STREAMS = 10

OUTER_RUNS = 10

CGROUP_DIR = Path("/sys/fs/cgroup/system.slice/nginx.service")
CPU_STAT_PATH = CGROUP_DIR / "cpu.stat"
MEMORY_CURRENT_PATH = CGROUP_DIR / "memory.current"

CSV_OUTPUT = Path(f"./results_test1_{CIPHER_LABEL}.csv")

MEMORY_POLL_INTERVAL_SECONDS = 0.01


# ============================================================
# Helpers
# ============================================================

def read_text_file(path: Path) -> str:
    with path.open("r", encoding="utf-8") as f:
        return f.read().strip()


def read_cpu_stat() -> dict:
    content = read_text_file(CPU_STAT_PATH)
    stats = {}
    for line in content.splitlines():
        parts = line.split()
        if len(parts) == 2:
            key, value = parts
            try:
                stats[key] = int(value)
            except ValueError:
                pass
    return stats


def read_memory_current() -> int:
    return int(read_text_file(MEMORY_CURRENT_PATH))


def bytes_to_mb(value: int) -> float:
    return value / (1024 * 1024)


def build_h2load_command() -> list:
    return [
        H2LOAD_BIN,
        f"--duration={H2LOAD_DURATION_SECONDS}",
        f"--clients={H2LOAD_CLIENTS}",
        f"--threads={H2LOAD_THREADS}",
        f"--max-concurrent-streams={H2LOAD_MAX_CONCURRENT_STREAMS}",
        TARGET_URL,
    ]


def monitor_memory(stop_event, samples):
    while not stop_event.is_set():
        try:
            samples.append(read_memory_current())
        except Exception:
            pass
        time.sleep(MEMORY_POLL_INTERVAL_SECONDS)


def convert_to_ms(value: str, unit: str) -> float:
    value = float(value)
    unit = unit.strip().lower()

    if unit == "us":
        return value / 1000.0
    if unit == "ms":
        return value
    if unit == "s":
        return value * 1000.0

    raise ValueError(f"Unsupported time unit: {unit}")


def parse_h2load_output(output: str) -> dict:
    result = {
        "requests_per_sec": "",
        "latency_min_ms": "",
        "latency_max_ms": "",
        "latency_mean_ms": "",
        "latency_sd_ms": "",
    }

    m = re.search(r"finished in\s+[0-9.]+s,\s+([0-9.]+)\s+req/s,", output)
    if m:
        result["requests_per_sec"] = m.group(1)

    m = re.search(
        r"time for request:\s+"
        r"([0-9.]+)(us|ms|s)\s+"
        r"([0-9.]+)(us|ms|s)\s+"
        r"([0-9.]+)(us|ms|s)\s+"
        r"([0-9.]+)(us|ms|s)",
        output
    )
    if m:
        result["latency_min_ms"] = round(convert_to_ms(m.group(1), m.group(2)), 3)
        result["latency_max_ms"] = round(convert_to_ms(m.group(3), m.group(4)), 3)
        result["latency_mean_ms"] = round(convert_to_ms(m.group(5), m.group(6)), 3)
        result["latency_sd_ms"] = round(convert_to_ms(m.group(7), m.group(8)), 3)

    return result



# ============================================================
# Main
# ============================================================

def main():
    CSV_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "run_id",
        "cpu_usage_delta_usec",
        "memory_peak_delta_mb",
        "requests_per_sec",
        "latency_min_ms",
        "latency_max_ms",
        "latency_mean_ms",
        "latency_sd_ms",
    ]

    rows = []

    for run_id in range(1, OUTER_RUNS + 1):
        print(f"\n========== Run {run_id}/{OUTER_RUNS} | Cipher: {CIPHER_LABEL} ==========\n")

        cpu_before = read_cpu_stat()
        memory_start = read_memory_current()

        memory_samples = [memory_start]
        stop_event = threading.Event()
        monitor_thread = threading.Thread(
            target=monitor_memory,
            args=(stop_event, memory_samples),
            daemon=True
        )

        cmd = build_h2load_command()
        print("Starting h2load:")
        print(" ".join(cmd))
        print()

        monitor_thread.start()

        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            check=False
        )

        stop_event.set()
        monitor_thread.join()

        cpu_after = read_cpu_stat()

        h2load_output = (proc.stdout or "") + "\n" + (proc.stderr or "")

        print("----- h2load output start -----")
        print(h2load_output.strip())
        print("----- h2load output end -------")

        peak_memory = max(memory_samples)
        peak_delta_mb = bytes_to_mb(peak_memory - memory_start)

        cpu_usage_delta = cpu_after.get("usage_usec", 0) - cpu_before.get("usage_usec", 0)

        parsed = parse_h2load_output(h2load_output)

        row = {
            "run_id": run_id,
            "cpu_usage_delta_usec": cpu_usage_delta,
            "memory_peak_delta_mb": round(peak_delta_mb, 3),
            "requests_per_sec": parsed["requests_per_sec"],
            "latency_min_ms": parsed["latency_min_ms"],
            "latency_max_ms": parsed["latency_max_ms"],
            "latency_mean_ms": parsed["latency_mean_ms"],
            "latency_sd_ms": parsed["latency_sd_ms"],
        }

        rows.append(row)

        print(
            f"\nRun {run_id} summary: "
            f"CPU delta={cpu_usage_delta} usec, "
            f"RAM peak delta={peak_delta_mb:.3f} MB, "
            f"req/s={parsed['requests_per_sec']}, "
            f"latency mean={parsed['latency_mean_ms']} ms\n"
        )

    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCSV written to: {CSV_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
