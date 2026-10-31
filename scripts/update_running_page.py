from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_ROOT = REPO_ROOT / "roadnotfixed-blog"
DEFAULT_DASHBOARD_ROOT = Path(r"E:\Codex\apple_watch_local_dashboard")
DEFAULT_EXPORT_ZIP = REPO_ROOT.parent / "health" / "export.zip"
RUNNING_WHERE = "lower(workout_type) LIKE '%running%' OR workout_type LIKE '%跑步%'"


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} not found: {resolved}")
    return resolved


def listener_active(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def latest_run_date(db_path: Path) -> str:
    with sqlite3.connect(str(db_path)) as connection:
        value = connection.execute(
            f"SELECT MAX(start_date) FROM workouts WHERE {RUNNING_WHERE}"
        ).fetchone()[0]
    if not value:
        raise RuntimeError("No existing running workout was found; use the full importer first.")
    return str(value)[:10]


def database_run_stats(db_path: Path) -> tuple[int, str, int]:
    with sqlite3.connect(str(db_path)) as connection:
        count, latest = connection.execute(
            f"SELECT COUNT(*), MAX(start_date) FROM workouts WHERE {RUNNING_WHERE}"
        ).fetchone()
        identities = connection.execute(
            f"""
            SELECT workout_type, COALESCE(source_name, ''), start_date,
                   COALESCE(end_date, start_date)
            FROM workouts WHERE {RUNNING_WHERE}
            """
        ).fetchall()
    normalized = Counter(
        (
            workout_type,
            source_name,
            datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(),
            datetime.fromisoformat(end.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat(),
        )
        for workout_type, source_name, start, end in identities
    )
    duplicate_groups = sum(1 for item_count in normalized.values() if item_count > 1)
    return int(count), str(latest), int(duplicate_groups)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def public_stats(json_path: Path) -> tuple[int, float, int, int, str]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    activities = payload.get("activities", [])
    return (
        len(activities),
        round(sum(float(item.get("distance_km") or 0) for item in activities), 1),
        sum(item.get("avg_hr_bpm") is not None for item in activities),
        sum(bool(item.get("route")) for item in activities),
        max((str(item.get("date") or "") for item in activities), default=""),
    )


def run(command: list[str], cwd: Path) -> None:
    print(f"\n> {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=str(cwd), check=True)


def update_from_zip(export_zip: Path) -> int:
    source_zip = require_file(export_zip, "export.zip")
    export_script = require_file(REPO_ROOT / "scripts" / "export_running_data.py", "Running exporter")
    output_json = SITE_ROOT / "static" / "running" / "data.json"
    print(f"Apple Health export: {source_zip}", flush=True)
    backup_path = None
    with tempfile.TemporaryDirectory(prefix="running-page-") as temporary:
        temporary_root = Path(temporary)
        candidate_json = temporary_root / "data.json"
        run(
            [sys.executable, str(export_script), "--health-zip", str(source_zip), "--output", str(candidate_json)],
            REPO_ROOT,
        )
        public = public_stats(candidate_json)
        if output_json.exists():
            before = public_stats(output_json)
            print(f"Public runs: {before[0]} -> {public[0]}; distance: {before[1]:.1f} -> {public[1]:.1f} km")
            backup_dir = source_zip.parent / "backups"
            backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = backup_dir / f"running-data-before-{timestamp}.json"
            shutil.copy2(output_json, backup_path)

        output_json.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(candidate_json, output_json)
            build_dir = temporary_root / "site"
            run(
                ["hugo", "--minify", "--baseURL", "https://roadnotfixed.github.io/", "--destination", str(build_dir)],
                SITE_ROOT,
            )
            built_json = require_file(build_dir / "running" / "data.json", "built Running JSON")
            if sha256(output_json) != sha256(built_json):
                raise RuntimeError("Source and built Running JSON hashes do not match.")
        except Exception:
            if backup_path is not None:
                shutil.copy2(backup_path, output_json)
            raise

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    latest_year = public[4][:4]
    year_items = [item for item in payload["activities"] if item["date"].startswith(latest_year + "-")]
    year_distance = sum(float(item["distance_km"]) for item in year_items)
    print(
        f"\nRunning Page refreshed: runs={public[0]}, total_km={public[1]:.1f}, "
        f"HR={public[2]}, routes={public[3]}, latest={public[4]}"
    )
    print(f"{latest_year} YTD: {year_distance:.1f} km / {len(year_items)} runs")
    if backup_path is not None:
        print(f"Previous Running JSON backup: {backup_path}")
    print("No commit or push was performed.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Refresh Running Page from an Apple Health ZIP and validate Hugo; optionally use the legacy dashboard."
    )
    parser.add_argument("--export-zip", type=Path, help=f"Apple Health ZIP (default: {DEFAULT_EXPORT_ZIP})")
    parser.add_argument("--dashboard-root", type=Path, help="Use the legacy dashboard database/importer instead")
    parser.add_argument("--since", help="Override the automatic overlap date (YYYY-MM-DD)")
    args = parser.parse_args()

    if args.since:
        try:
            date.fromisoformat(args.since)
        except ValueError:
            parser.error("--since must use YYYY-MM-DD")

    if args.export_zip is not None and args.dashboard_root is not None:
        parser.error("--export-zip and --dashboard-root cannot be used together")
    if args.export_zip is not None or (args.dashboard_root is None and DEFAULT_EXPORT_ZIP.is_file()):
        if args.since:
            parser.error("--since applies only to the legacy dashboard import; direct ZIP mode reads the full running history")
        return update_from_zip(args.export_zip or DEFAULT_EXPORT_ZIP)

    dashboard_root = (args.dashboard_root or DEFAULT_DASHBOARD_ROOT).expanduser().resolve()
    db_path = require_file(dashboard_root / "data" / "health.db", "health.db")
    export_zip = require_file(dashboard_root / "import" / "export.zip", "export.zip")
    dashboard_python = require_file(dashboard_root / "venv" / "Scripts" / "python.exe", "dashboard Python")
    import_script = require_file(dashboard_root / "scripts" / "import_health.py", "health importer")
    export_script = require_file(REPO_ROOT / "scripts" / "export_running_data.py", "Running exporter")
    output_json = (SITE_ROOT / "static" / "running" / "data.json").resolve()

    active_ports = [port for port in (8501, 8765) if listener_active(port)]
    if active_ports:
        raise RuntimeError(f"Stop the dashboard/receiver first; active ports: {active_ports}")

    since = args.since or latest_run_date(db_path)
    before = database_run_stats(db_path)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = (db_path.parent / f"health-before-{timestamp}.db").resolve()
    if backup_path.exists():
        raise FileExistsError(backup_path)

    print(f"Incremental overlap date: {since}")
    print(f"Database before: runs={before[0]}, latest={before[1]}, duplicate_groups={before[2]}")
    print(f"Backing up {db_path} -> {backup_path}", flush=True)
    shutil.copy2(db_path, backup_path)

    run(
        [
            str(dashboard_python),
            str(import_script),
            str(export_zip),
            "--db",
            str(db_path),
            "--since",
            since,
            "--running-only",
        ],
        dashboard_root,
    )

    after = database_run_stats(db_path)
    if after[2] != 0:
        raise RuntimeError(f"Semantic duplicate workout groups detected: {after[2]}")
    print(f"Database after: runs={after[0]}, latest={after[1]}, duplicate_groups={after[2]}")

    run(
        [
            str(dashboard_python),
            str(export_script),
            "--db",
            str(db_path),
            "--routes-zip",
            str(export_zip),
        ],
        REPO_ROOT,
    )

    check_dir = (SITE_ROOT / ".hugo-check").resolve()
    expected_check_dir = (SITE_ROOT / ".hugo-check").resolve()
    try:
        run(
            [
                "hugo",
                "--minify",
                "--baseURL",
                "https://roadnotfixed.github.io/",
                "--destination",
                str(check_dir),
                "--cleanDestinationDir",
            ],
            SITE_ROOT,
        )
        built_json = require_file(check_dir / "running" / "data.json", "built Running JSON")
        if sha256(output_json) != sha256(built_json):
            raise RuntimeError("Source and built Running JSON hashes do not match.")
    finally:
        if check_dir.exists():
            if check_dir != expected_check_dir or check_dir.parent != SITE_ROOT.resolve():
                raise RuntimeError(f"Refusing to remove unexpected build directory: {check_dir}")
            shutil.rmtree(check_dir)

    public = public_stats(output_json)
    print(
        "\nRunning Page refreshed: "
        f"runs={public[0]}, total_km={public[1]:.1f}, HR={public[2]}, "
        f"routes={public[3]}, latest={public[4]}"
    )
    print(f"Database backup: {backup_path}")
    print("No commit or push was performed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
