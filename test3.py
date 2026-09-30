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
OUTER_RUNS = 10
HANDSHAKES_PER_BLOCK = 100
TIMEOUT_SECONDS = 10

CGROUP_DIR = Path("/sys/fs/cgroup/system.slice/nginx.service")
CPU_STAT_PATH = CGROUP_DIR / "cpu.stat"

SESSION_BASE_DIR = Path("./tls_sessions")
CSV_OUTPUT = Path("./results_test3_session_resumption_10x100.csv")

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


def build_openssl_command(sess_out: Path = None, sess_in: Path = None) -> list:
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

    if sess_out is not None:
        cmd.extend(["-sess_out", str(sess_out)])

    if sess_in is not None:
        cmd.extend(["-sess_in", str(sess_in)])

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
    SESSION_BASE_DIR.mkdir(parents=True, exist_ok=True)
    CSV_OUTPUT.parent.mkdir(parents=True, exist_ok=True)

    full_times_all_runs = []
    resumed_times_all_runs = []

    avg_full_times = []
    avg_resumed_times = []

    cpu_full_deltas = []
    cpu_resumed_deltas = []

    for run_idx in range(1, OUTER_RUNS + 1):
        print(f"\n========== Durchlauf {run_idx}/{OUTER_RUNS} ==========")

        run_session_dir = SESSION_BASE_DIR / f"run_{run_idx:02d}"
        run_session_dir.mkdir(parents=True, exist_ok=True)

        session_files = [
            run_session_dir / f"session_{i:03d}.pem"
            for i in range(1, HANDSHAKES_PER_BLOCK + 1)
        ]

        for session_file in session_files:
            if session_file.exists():
                session_file.unlink()

        # ----------------------------------------------------
        # Block 1: 100 vollständige Handshakes
        # ----------------------------------------------------
        cpu_before_full = read_cpu_stat()

        full_times = []
        for i, session_file in enumerate(session_files, start=1):
            cmd = build_openssl_command(sess_out=session_file)
            duration_ms = run_handshake(cmd, TIMEOUT_SECONDS)
            full_times.append(round(duration_ms, 3))
            print(f"Run {run_idx:02d} | Full {i:03d}/{HANDSHAKES_PER_BLOCK}: {duration_ms:.3f} ms")

            if SLEEP_BETWEEN_HANDSHAKES_SECONDS > 0:
                time.sleep(SLEEP_BETWEEN_HANDSHAKES_SECONDS)

        cpu_after_full = read_cpu_stat()

        full_cpu_delta = cpu_after_full.get("usage_usec", 0) - cpu_before_full.get("usage_usec", 0)

        # ----------------------------------------------------
        # Block 2: 100 verkürzte Handshakes
        # ----------------------------------------------------
        cpu_before_resumed = read_cpu_stat()

        resumed_times = []
        for i, session_file in enumerate(session_files, start=1):
            cmd = build_openssl_command(sess_in=session_file)
            duration_ms = run_handshake(cmd, TIMEOUT_SECONDS)
            resumed_times.append(round(duration_ms, 3))
            print(f"Run {run_idx:02d} | Resumed {i:03d}/{HANDSHAKES_PER_BLOCK}: {duration_ms:.3f} ms")

            if SLEEP_BETWEEN_HANDSHAKES_SECONDS > 0:
                time.sleep(SLEEP_BETWEEN_HANDSHAKES_SECONDS)

        cpu_after_resumed = read_cpu_stat()

        resumed_cpu_delta = cpu_after_resumed.get("usage_usec", 0) - cpu_before_resumed.get("usage_usec", 0)

        full_times_all_runs.append(full_times)
        resumed_times_all_runs.append(resumed_times)

        avg_full_times.append(statistics.fmean(full_times))
        avg_resumed_times.append(statistics.fmean(resumed_times))

        cpu_full_deltas.append(full_cpu_delta)
        cpu_resumed_deltas.append(resumed_cpu_delta)

    # --------------------------------------------------------
    # CSV layout
    #
    # Row 1:   header
    # Row 2-101: session IDs 1..100 + alternating full/resumed columns per run
    # Row 102: TIME avg
    # Row 103: empty
    # Row 104: CPU values
    # --------------------------------------------------------
    header = ["Durchlaufs-ID"]
    for run_idx in range(1, OUTER_RUNS + 1):
        header.append(f"Run_{run_idx:02d}_Full_ms")
        header.append(f"Run_{run_idx:02d}_Resumed_ms")

    rows = [header]

    for session_idx in range(HANDSHAKES_PER_BLOCK):
        row = [session_idx + 1]
        for run_idx in range(OUTER_RUNS):
            row.append(full_times_all_runs[run_idx][session_idx])
            row.append(resumed_times_all_runs[run_idx][session_idx])
        rows.append(row)

    avg_row = ["TIME avg (ms)"]
    for run_idx in range(OUTER_RUNS):
        avg_row.append(round(avg_full_times[run_idx], 3))
        avg_row.append(round(avg_resumed_times[run_idx], 3))
    rows.append(avg_row)

    rows.append([])

    cpu_row = ["CPU usage delta (usec)"]
    for run_idx in range(OUTER_RUNS):
        cpu_row.append(cpu_full_deltas[run_idx])
        cpu_row.append(cpu_resumed_deltas[run_idx])
    rows.append(cpu_row)

    with CSV_OUTPUT.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print("\n================ Zusammenfassung ================\n")
    for run_idx in range(OUTER_RUNS):
        print(
            f"Run {run_idx + 1:02d}: "
            f"TIME full avg={avg_full_times[run_idx]:.3f} ms, "
            f"TIME resumed avg={avg_resumed_times[run_idx]:.3f} ms, "
            f"CPU full={cpu_full_deltas[run_idx]} usec, "
            f"CPU resumed={cpu_resumed_deltas[run_idx]} usec, "
        )

    avg_cpu_full = sum(cpu_full_deltas) / len(cpu_full_deltas)
    avg_cpu_resumed = sum(cpu_resumed_deltas) / len(cpu_resumed_deltas)
    avg_time_full = statistics.fmean(avg_full_times)
    avg_time_resumed = statistics.fmean(avg_resumed_times)

    print("\nMittelwerte über 10 Durchläufe:")
    print(f"TIME full avg: {avg_time_full:.3f} ms")
    print(f"TIME resumed avg: {avg_time_resumed:.3f} ms")
    print(f"CPU full avg: {avg_cpu_full:.2f} usec")
    print(f"CPU resumed avg: {avg_cpu_resumed:.2f} usec")

    print(f"\nCSV written to: {CSV_OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
