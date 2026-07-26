from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "oldifnotwild-blog" / "static" / "running" / "data.json"


def read_runs(db_path: Path) -> list[dict[str, object]]:
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Apple Health database not found: {resolved}")

    uri_path = quote(resolved.as_posix(), safe="/:")
    connection = sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        rows = connection.execute(
            """
            SELECT start_date, duration_min, total_distance_km
            FROM workouts
            WHERE workout_type = 'HKWorkoutActivityTypeRunning'
               OR workout_type LIKE '%Running%'
               OR workout_type LIKE '%跑步%'
            ORDER BY start_date
            """
        ).fetchall()
    finally:
        connection.close()

    activities: list[dict[str, object]] = []
    for start_date, duration_min, distance_km in rows:
        if not start_date or duration_min is None or distance_km is None:
            continue
        duration = float(duration_min)
        distance = float(distance_km)
        if duration <= 0 or distance <= 0:
            continue

        # Apple Health dates include the local offset. Publishing only the
        # calendar date removes the exact workout time and timezone.
        local_date = str(start_date).split("T", 1)[0].split(" ", 1)[0]
        if len(local_date) != 10:
            continue

        activities.append(
            {
                "date": local_date,
                "distance_km": round(distance, 3),
                "duration_min": round(duration, 1),
                "pace_min_km": round(duration / distance, 2),
            }
        )
    return activities


def build_payload(activities: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "generated_date": datetime.now(timezone.utc).date().isoformat(),
        "activities": activities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export privacy-filtered Apple Health running data for the Hugo site."
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to health.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    activities = read_runs(args.db)
    if not activities:
        raise SystemExit("No valid running workouts were found; output was not changed.")

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(build_payload(activities), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    total_distance = sum(float(item["distance_km"]) for item in activities)
    print(
        f"Exported {len(activities)} runs / {total_distance:.1f} km to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
