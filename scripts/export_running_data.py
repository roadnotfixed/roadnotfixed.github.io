from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "roadnotfixed-blog" / "static" / "running" / "data.json"
TIME_RE = re.compile(br"<time>([^<]+)</time>")


@dataclass
class Workout:
    workout_hash: str
    start_text: str
    start_utc: datetime
    end_utc: datetime
    date: str
    duration_min: float
    distance_km: float
    avg_hr_bpm: Optional[int]


@dataclass
class Route:
    member: str
    start_utc: datetime
    end_utc: datetime


@dataclass
class RoutePoint:
    timestamp: datetime
    latitude: float
    longitude: float


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def connect_read_only(db_path: Path) -> sqlite3.Connection:
    resolved = db_path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Apple Health database not found: {resolved}")
    uri_path = quote(resolved.as_posix(), safe="/:")
    return sqlite3.connect(
        f"file:{uri_path}?mode=ro&immutable=1",
        uri=True,
    )


def read_workouts(db_path: Path, min_distance_km: float) -> list[Workout]:
    connection = connect_read_only(db_path)
    try:
        rows = connection.execute(
            """
            SELECT w.workout_hash, w.start_date, w.end_date,
                   w.duration_min, w.total_distance_km, hr.avg_hr
            FROM workouts AS w
            LEFT JOIN (
                SELECT workout_hash, AVG(avg_value) AS avg_hr
                FROM workout_stats
                WHERE type='HKQuantityTypeIdentifierHeartRate'
                  AND avg_value BETWEEN 20 AND 260
                GROUP BY workout_hash
            ) AS hr ON hr.workout_hash=w.workout_hash
            WHERE w.workout_type = 'HKWorkoutActivityTypeRunning'
               OR w.workout_type LIKE '%Running%'
               OR w.workout_type LIKE '%跑步%'
            ORDER BY w.start_date
            """
        ).fetchall()
    finally:
        connection.close()

    workouts: list[Workout] = []
    for workout_hash, start_text, end_text, duration_min, distance_km, avg_hr in rows:
        if not start_text or not end_text or duration_min is None or distance_km is None:
            continue
        duration = float(duration_min)
        distance = float(distance_km)
        if duration <= 0 or distance < min_distance_km:
            continue
        start = parse_datetime(str(start_text))
        end = parse_datetime(str(end_text))
        workouts.append(
            Workout(
                workout_hash=str(workout_hash),
                start_text=str(start_text),
                start_utc=start.astimezone(timezone.utc),
                end_utc=end.astimezone(timezone.utc),
                date=str(start_text).split("T", 1)[0].split(" ", 1)[0],
                duration_min=duration,
                distance_km=distance,
                avg_hr_bpm=int(round(float(avg_hr))) if avg_hr is not None else None,
            )
        )
    return workouts


def index_routes(archive: zipfile.ZipFile, earliest_date: str) -> list[Route]:
    cutoff = datetime.fromisoformat(earliest_date).date() - timedelta(days=1)
    routes: list[Route] = []
    for info in archive.infolist():
        if not info.filename.lower().endswith(".gpx"):
            continue
        match = re.search(r"route_(\d{4}-\d{2}-\d{2})_", info.filename, re.IGNORECASE)
        if match and datetime.fromisoformat(match.group(1)).date() < cutoff:
            continue
        times = TIME_RE.findall(archive.read(info))
        if len(times) < 3:
            continue
        try:
            start = parse_datetime(times[1].decode("utf-8")).astimezone(timezone.utc)
            end = parse_datetime(times[-1].decode("utf-8")).astimezone(timezone.utc)
        except (UnicodeDecodeError, ValueError):
            continue
        if end >= start:
            routes.append(Route(info.filename, start, end))
    return routes


def match_routes(workouts: list[Workout], routes: list[Route]) -> dict[str, Route]:
    candidates: list[tuple[float, float, float, int, int]] = []
    tolerance = timedelta(minutes=5)
    for workout_index, workout in enumerate(workouts):
        for route_index, route in enumerate(routes):
            if route.end_utc < workout.start_utc - tolerance:
                continue
            if route.start_utc > workout.end_utc + tolerance:
                continue
            overlap = max(
                0.0,
                (
                    min(workout.end_utc, route.end_utc)
                    - max(workout.start_utc, route.start_utc)
                ).total_seconds(),
            )
            route_duration = max(1.0, (route.end_utc - route.start_utc).total_seconds())
            overlap_ratio = overlap / route_duration
            start_delta = abs((route.start_utc - workout.start_utc).total_seconds())
            if overlap_ratio >= 0.5:
                candidates.append(
                    (overlap_ratio, overlap, -start_delta, workout_index, route_index)
                )

    assigned_workouts: set[int] = set()
    assigned_routes: set[int] = set()
    matches: dict[str, Route] = {}
    for _ratio, _overlap, _delta, workout_index, route_index in sorted(
        candidates, reverse=True
    ):
        if workout_index in assigned_workouts or route_index in assigned_routes:
            continue
        assigned_workouts.add(workout_index)
        assigned_routes.add(route_index)
        matches[workouts[workout_index].workout_hash] = routes[route_index]
    return matches


def child_text(element: ET.Element, local_name: str) -> Optional[str]:
    for child in element:
        if child.tag.rsplit("}", 1)[-1] == local_name:
            return child.text
    return None


def load_route_points(archive: zipfile.ZipFile, member: str) -> list[RoutePoint]:
    root = ET.fromstring(archive.read(member))
    points: list[RoutePoint] = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "trkpt":
            continue
        try:
            timestamp_text = child_text(element, "time")
            if not timestamp_text:
                continue
            points.append(
                RoutePoint(
                    timestamp=parse_datetime(timestamp_text).astimezone(timezone.utc),
                    latitude=float(element.attrib["lat"]),
                    longitude=float(element.attrib["lon"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    points.sort(key=lambda point: point.timestamp)
    return points


def haversine_m(left: RoutePoint, right: RoutePoint) -> float:
    radius = 6371000.0
    lat1 = math.radians(left.latitude)
    lat2 = math.radians(right.latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(right.longitude - left.longitude)
    value = (
        math.sin(delta_lat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2.0) ** 2
    )
    return radius * 2.0 * math.atan2(
        math.sqrt(value), math.sqrt(max(0.0, 1.0 - value))
    )


def longest_continuous_segment(points: list[RoutePoint]) -> list[RoutePoint]:
    if len(points) < 2:
        return []
    segments: list[list[RoutePoint]] = [[points[0]]]
    for previous, current in zip(points, points[1:]):
        if haversine_m(previous, current) <= 250.0:
            segments[-1].append(current)
        else:
            segments.append([current])
    return max(segments, key=len)


def privacy_route(
    points: list[RoutePoint], trim_meters: float, max_points: int
) -> list[list[float]]:
    points = longest_continuous_segment(points)
    if len(points) < 2:
        return []

    distances = [0.0]
    for previous, current in zip(points, points[1:]):
        distances.append(distances[-1] + haversine_m(previous, current))
    measured = distances[-1]
    if measured <= 0:
        return []

    trim = min(max(0.0, trim_meters), measured * 0.15)
    selected = [
        point
        for point, distance in zip(points, distances)
        if trim <= distance <= measured - trim
    ]
    if len(selected) < 2:
        return []

    if len(selected) > max_points:
        indices = {
            round(index * (len(selected) - 1) / (max_points - 1))
            for index in range(max_points)
        }
        selected = [selected[index] for index in sorted(indices)]

    public_points: list[list[float]] = []
    for point in selected:
        rounded = [round(point.latitude, 4), round(point.longitude, 4)]
        if not public_points or public_points[-1] != rounded:
            public_points.append(rounded)
    return public_points if len(public_points) >= 2 else []


def build_activities(
    workouts: list[Workout],
    routes_zip: Optional[Path],
    trim_meters: float,
    max_route_points: int,
) -> tuple[list[dict[str, object]], int]:
    matches: dict[str, Route] = {}
    archive: Optional[zipfile.ZipFile] = None
    if routes_zip is not None:
        route_path = routes_zip.expanduser().resolve()
        if not route_path.is_file():
            raise FileNotFoundError(f"Apple Health export ZIP not found: {route_path}")
        archive = zipfile.ZipFile(route_path)
        routes = index_routes(archive, workouts[0].date)
        matches = match_routes(workouts, routes)

    activities: list[dict[str, object]] = []
    route_count = 0
    try:
        for workout in workouts:
            item: dict[str, object] = {
                "date": workout.date,
                "distance_km": round(workout.distance_km, 3),
                "duration_min": round(workout.duration_min, 1),
                "pace_min_km": round(workout.duration_min / workout.distance_km, 2),
                "avg_hr_bpm": workout.avg_hr_bpm,
            }
            route = matches.get(workout.workout_hash)
            if route is not None and archive is not None:
                public_route = privacy_route(
                    load_route_points(archive, route.member),
                    trim_meters=trim_meters,
                    max_points=max_route_points,
                )
                if public_route:
                    item["route"] = public_route
                    route_count += 1
            activities.append(item)
    finally:
        if archive is not None:
            archive.close()
    return activities, route_count


def build_payload(
    activities: list[dict[str, object]], trim_meters: float, min_distance_km: float
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "generated_date": datetime.now(timezone.utc).date().isoformat(),
        "minimum_distance_km": min_distance_km,
        "route_privacy": {
            "endpoint_trim_meters": round(trim_meters),
            "coordinate_decimals": 4,
            "timestamps_removed": True,
        },
        "activities": activities,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export privacy-filtered Apple Health running data for the Hugo site."
    )
    parser.add_argument("--db", required=True, type=Path, help="Path to health.db")
    parser.add_argument(
        "--min-distance-km",
        type=float,
        default=2.0,
        help="Exclude runs shorter than this distance (default: 2.0)",
    )
    parser.add_argument(
        "--routes-zip",
        type=Path,
        help="Optional Apple Health export ZIP containing workout-routes GPX files",
    )
    parser.add_argument(
        "--trim-meters",
        type=float,
        default=500.0,
        help="Maximum distance removed from both route endpoints (default: 500)",
    )
    parser.add_argument(
        "--max-route-points",
        type=int,
        default=120,
        help="Maximum public coordinate points per route (default: 120)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    if args.max_route_points < 2:
        raise SystemExit("--max-route-points must be at least 2")
    if args.min_distance_km < 0:
        raise SystemExit("--min-distance-km cannot be negative")
    workouts = read_workouts(args.db, args.min_distance_km)
    if not workouts:
        raise SystemExit("No valid running workouts were found; output was not changed.")
    activities, route_count = build_activities(
        workouts,
        routes_zip=args.routes_zip,
        trim_meters=args.trim_meters,
        max_route_points=args.max_route_points,
    )

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            build_payload(activities, args.trim_meters, args.min_distance_km),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    total_distance = sum(float(item["distance_km"]) for item in activities)
    heart_rate_count = sum(item["avg_hr_bpm"] is not None for item in activities)
    print(
        f"Exported {len(activities)} runs / {total_distance:.1f} km / "
        f"{heart_rate_count} heart-rate summaries / {route_count} privacy-trimmed routes "
        f"to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
