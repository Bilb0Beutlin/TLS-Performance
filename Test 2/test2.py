#!/usr/bin/env python3

import csv
import statistics
import subprocess
import time
from pathlib import Path

# ============================================================
# Hardcoded configuration
# ============================================================

OPENSSL_BIN = "openssl"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 443
SERVER_NAME = "localhost"
CA_CERT_PATH = "./certs/ca.crt"

TLS_VERSION = "1.3"
CERT_LABEL = "ecdsa"
OUTER_RUNS = 10
HANDSHAKES_PER_RUN = 100
TIMEOUT_SECONDS = 10

CGROUP_DIR = Path("/sys/fs/cgroup/system.slice/nginx.service")
CPU_STAT_PATH = CGROUP_DIR / "cpu.stat"

CSV_OUTPUT = Path(f"./results_test2_certificate_{CERT_LABEL}.csv")

SLEEP_BETWEEN_HANDSHAKES_SECONDS = 0.0


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


def bytes_to_mib(value: int) -> float:
    return value / (1024 * 1024)


def build_openssl_command() -> list:
    cmd = [
        OPENSSL_BIN,
        "s_client",
        "-connect", f"{SERVER_HOST}:{SERVER_PORT}",
        "-servername", SERVER_NAME,
        "-brief",
        "-CAfile", CA_CERT_PATH,
    ]

    if TLS_VERSION == "1.2":
        cmd.append("-tls1_2")
    elif TLS_VERSION == "1.3":
        cmd.append("-tls1_3")
    else:
        raise ValueError(f"Unsupported TLS version: {TLS_VERSION}")

    return cmd


def run_handshake(cmd: list, timeout_seconds: int) -> float:
    start_ns = time.perf_counter_ns()
    subprocess.run(
        cmd,
        input="Q\n",
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    end_ns = time.perf_counter_ns()
    return (end_ns - start_ns) / 1_000_000.0


# ============================================================
# Main
# ============================================================

def main():
    CSV_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    all_times = []
    avg_times = []
    cpu_deltas = []

    for run_idx in range(1, OUTER_RUNS + 1):
        print(f"\n========== Durchlauf {run_idx}/{OUTER_RUNS} | Zertifikat: {CERT_LABEL} ==========")

        cpu_before = read_cpu_stat()

        handshake_times = []
        for i in range(1, HANDSHAKES_PER_RUN + 1):
            cmd = build_openssl_command()
            duration_ms = run_handshake(cmd, TIMEOUT_SECONDS)
            handshake_times.append(round(duration_ms, 3))

            print(f"Run {run_idx:02d} | Handshake {i:03d}/{HANDSHAKES_PER_RUN}: {duration_ms:.3f} ms")

            if SLEEP_BETWEEN_HANDSHAKES_SECONDS > 0:
                time.sleep(SLEEP_BETWEEN_HANDSHAKES_SECONDS)

        cpu_after = read_cpu_stat()

        cpu_delta = cpu_after.get("usage_usec", 0) - cpu_before.get("usage_usec", 0)
        time_avg = statistics.fmean(handshake_times)

        all_times.append(handshake_times)
        avg_times.append(time_avg)
        cpu_deltas.append(cpu_delta)

    # --------------------------------------------------------
    # CSV layout
    #
    # Row 1: header
    # Row 2-101: measurement id 1..100 + one column per run
    # Row 102: avg time
    # Row 103: empty
    # Row 104: CPU values
    # --------------------------------------------------------
    header = ["Durchlaufs-ID"]
    for run_idx in range(1, OUTER_RUNS + 1):
        header.append(f"Run_{run_idx:02d}_Handshake_ms")

    rows = [header]

    for measurement_idx in range(HANDSHAKES_PER_RUN):
        row = [measurement_idx + 1]
        for run_idx in range(OUTER_RUNS):
            row.append(all_times[run_idx][measurement_idx])
        rows.append(row)

    avg_row = ["TIME avg (ms)"]
    for run_idx in range(OUTER_RUNS):
        avg_row.append(round(avg_times[run_idx], 3))
    rows.append(avg_row)

    rows.append([])

    cpu_row = ["CPU usage delta (usec)"]
    for run_idx in range(OUTER_RUNS):
        cpu_row.append(cpu_deltas[run_idx])
    rows.append(cpu_row)

    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print("\n================ Zusammenfassung ================\n")
    print(f"Zertifikat: {CERT_LABEL}")
    for run_idx in range(OUTER_RUNS):
        print(
            f"Run {run_idx + 1:02d}: "
            f"TIME avg={avg_times[run_idx]:.3f} ms, "
            f"CPU={cpu_deltas[run_idx]} usec, "
        )

    avg_cpu = sum(cpu_deltas) / len(cpu_deltas)
    avg_time_overall = statistics.fmean(avg_times)

    print("\nMittelwerte über 10 Durchläufe:")
    print(f"TIME avg overall: {avg_time_overall:.3f} ms")
    print(f"CPU avg:          {avg_cpu:.2f} usec")

    print(f"\nCSV written to: {CSV_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
