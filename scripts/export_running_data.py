from __future__ import annotations

import argparse
import hashlib
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
STATISTICS_START_DATE = "2022-01-01"
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
    source_name: str = ""


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
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S %z")
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
                   w.duration_min, w.total_distance_km, hr.avg_hr, w.source_name
            FROM workouts AS w
            LEFT JOIN (
                SELECT workout_hash, AVG(avg_value) AS avg_hr
                FROM workout_stats
                WHERE type='HKQuantityTypeIdentifierHeartRate'
                  AND avg_value BETWEEN 20 AND 260
                GROUP BY workout_hash
            ) AS hr ON hr.workout_hash=w.workout_hash
            WHERE (w.workout_type = 'HKWorkoutActivityTypeRunning'
               OR w.workout_type LIKE '%Running%'
               OR w.workout_type LIKE '%跑步%')
              AND w.start_date >= ?
            ORDER BY w.start_date
            """,
            (STATISTICS_START_DATE,),
        ).fetchall()
    finally:
        connection.close()

    workouts: list[Workout] = []
    for workout_hash, start_text, end_text, duration_min, distance_km, avg_hr, source_name in rows:
        if not start_text or not end_text or duration_min is None or distance_km is None:
            continue
        duration = float(duration_min)
        distance = float(distance_km)
        if not math.isfinite(duration) or not math.isfinite(distance):
            continue
        if duration <= 0 or distance <= 0 or distance < min_distance_km:
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
                source_name=str(source_name or ""),
            )
        )
    return workouts


def read_export_workout(element: ET.Element, min_distance_km: float) -> Optional[Workout]:
    attrs = element.attrib
    workout_type = attrs.get("workoutActivityType", "")
    if "running" not in workout_type.lower() and "跑步" not in workout_type:
        return None
    # Match the local workout date used by the year/month views, not the UTC day.
    if attrs.get("startDate", "")[:10] < STATISTICS_START_DATE:
        return None

    distance_value = attrs.get("totalDistance")
    distance_unit = attrs.get("totalDistanceUnit", "km")
    heart_rates: list[float] = []
    for child in element:
        if child.tag.rsplit("}", 1)[-1] != "WorkoutStatistics":
            continue
        stat = child.attrib
        if stat.get("type") == "HKQuantityTypeIdentifierDistanceWalkingRunning":
            if distance_value is None:
                distance_value = stat.get("sum")
                distance_unit = stat.get("unit", "km")
        elif stat.get("type") == "HKQuantityTypeIdentifierHeartRate":
            try:
                average = float(stat["average"])
                if 20 <= average <= 260:
                    heart_rates.append(average)
            except (KeyError, TypeError, ValueError):
                pass

    distance_factors = {"km": 1.0, "m": 0.001, "mi": 1.609344}
    duration_factors = {"min": 1.0, "s": 1 / 60.0, "sec": 1 / 60.0, "h": 60.0, "hr": 60.0}
    try:
        distance = float(distance_value) * distance_factors[distance_unit]
        duration = float(attrs["duration"]) * duration_factors[attrs.get("durationUnit", "min")]
        start = parse_datetime(attrs["startDate"])
        end = parse_datetime(attrs["endDate"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(distance) or not math.isfinite(duration):
        return None
    if duration <= 0 or distance <= 0 or distance < min_distance_km or end < start:
        return None

    start_utc = start.astimezone(timezone.utc)
    end_utc = end.astimezone(timezone.utc)
    identity = json.dumps(
        [workout_type, attrs.get("sourceName", ""), start_utc.isoformat(), end_utc.isoformat()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return Workout(
        workout_hash=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        start_text=start.isoformat(),
        start_utc=start_utc,
        end_utc=end_utc,
        date=start.date().isoformat(),
        duration_min=duration,
        distance_km=distance,
        avg_hr_bpm=int(round(sum(heart_rates) / len(heart_rates))) if heart_rates else None,
        source_name=attrs.get("sourceName", ""),
    )


def read_workouts_from_zip(export_zip: Path, min_distance_km: float) -> list[Workout]:
    """Read only workouts; discard other XML nodes as they finish to bound memory."""
    workouts: dict[str, Workout] = {}
    seen = duplicates = 0
    with zipfile.ZipFile(export_zip) as archive:
        members = [
            info for info in archive.infolist()
            if info.filename.replace("\\", "/").rsplit("/", 1)[-1].lower() == "export.xml"
            and not info.filename.startswith("__MACOSX/")
        ]
        if len(members) != 1:
            raise ValueError("Expected exactly one export.xml inside the Apple Health ZIP.")
        with archive.open(members[0]) as stream:
            events = iter(ET.iterparse(stream, events=("start", "end")))
            _event, root = next(events)
            in_workout = False
            for event, element in events:
                tag = element.tag.rsplit("}", 1)[-1]
                if event == "start":
                    if tag == "Workout":
                        in_workout = True
                    continue
                if tag == "Workout":
                    seen += 1
                    workout = read_export_workout(element, min_distance_km)
                    if workout is not None:
                        duplicates += int(workout.workout_hash in workouts)
                        workouts[workout.workout_hash] = workout
                    in_workout = False
                if not in_workout:
                    element.clear()
                    root.clear()
    print(f"Scanned {seen} workouts; merged {duplicates} UTC duplicate runs.", flush=True)
    return sorted(workouts.values(), key=lambda workout: workout.start_utc)


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


def group_workouts(
    workouts: list[Workout], max_gap_minutes: float = 10.0, short_distance_km: float = 2.0
) -> list[list[Workout]]:
    """Join adjacent same-day/source recordings only when the chain contains a short run."""
    ordered = sorted(workouts, key=lambda workout: workout.start_utc)
    if max_gap_minutes <= 0:
        return [[workout] for workout in ordered]

    chains: list[list[Workout]] = []
    for workout in ordered:
        previous = chains[-1][-1] if chains else None
        gap = workout.start_utc - previous.end_utc if previous else None
        if (
            previous is not None
            and workout.date == previous.date
            and workout.source_name == previous.source_name
            and timedelta(0) <= gap <= timedelta(minutes=max_gap_minutes)
        ):
            chains[-1].append(workout)
        else:
            chains.append([workout])

    groups: list[list[Workout]] = []
    for chain in chains:
        if any(workout.distance_km < short_distance_km for workout in chain):
            groups.append(chain)
        else:
            groups.extend([workout] for workout in chain)
    return groups


def build_activities(
    workouts: list[Workout],
    routes_zip: Optional[Path],
    trim_meters: float,
    max_route_points: int,
    merge_gap_minutes: float = 10.0,
) -> tuple[list[dict[str, object]], int]:
    matches: dict[str, Route] = {}
    archive: Optional[zipfile.ZipFile] = None
    if routes_zip is not None:
        route_path = routes_zip.expanduser().resolve()
        if not route_path.is_file():
            raise FileNotFoundError(f"Apple Health export ZIP not found: {route_path}")
        archive = zipfile.ZipFile(route_path)
        routes = index_routes(archive, min(workout.date for workout in workouts))
        matches = match_routes(workouts, routes)

    activities: list[dict[str, object]] = []
    route_count = 0
    try:
        for group in group_workouts(workouts, merge_gap_minutes):
            distance = sum(workout.distance_km for workout in group)
            duration = sum(workout.duration_min for workout in group)
            with_heart_rate = [workout for workout in group if workout.avg_hr_bpm is not None]
            heart_rate_duration = sum(workout.duration_min for workout in with_heart_rate)
            average_hr = (
                int(round(sum(workout.avg_hr_bpm * workout.duration_min for workout in with_heart_rate) / heart_rate_duration))
                if heart_rate_duration > 0 else None
            )
            item: dict[str, object] = {
                "date": group[0].date,
                "distance_km": round(distance, 6),
                "duration_min": round(duration, 3),
                "pace_min_km": round(duration / distance, 2),
                "avg_hr_bpm": average_hr,
            }
            if len(group) > 1:
                item["segments"] = [
                    {
                        "distance_km": round(workout.distance_km, 6),
                        "duration_min": round(workout.duration_min, 3),
                        "avg_hr_bpm": workout.avg_hr_bpm,
                    }
                    for workout in group
                ]
            public_routes: list[list[list[float]]] = []
            for workout in group:
                route = matches.get(workout.workout_hash)
                if route is not None and archive is not None:
                    public_route = privacy_route(
                        load_route_points(archive, route.member),
                        trim_meters=trim_meters,
                        max_points=max_route_points,
                    )
                    if public_route:
                        public_routes.append(public_route)
            if public_routes:
                if len(public_routes) == 1:
                    item["route"] = public_routes[0]
                else:
                    # Preserve separate, independently trimmed polylines; never join hidden endpoints.
                    item["route_segments"] = public_routes
                route_count += 1
            activities.append(item)
    finally:
        if archive is not None:
            archive.close()
    return activities, route_count


def build_payload(
    activities: list[dict[str, object]], trim_meters: float, min_distance_km: float,
    merge_gap_minutes: float = 10.0,
) -> dict[str, object]:
    return {
        "schema_version": 3,
        "generated_date": datetime.now(timezone.utc).date().isoformat(),
        "statistics_start_date": STATISTICS_START_DATE,
        "minimum_distance_km": min_distance_km,
        "session_merge": {
            "max_gap_minutes": merge_gap_minutes,
            "requires_segment_shorter_than_km": 2.0,
            "same_day": True,
            "same_source": True,
        },
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
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path, help="Path to health.db")
    source.add_argument("--health-zip", type=Path, help="Read workouts and routes directly from an Apple Health ZIP")
    parser.add_argument(
        "--min-distance-km",
        type=float,
        default=0.0,
        help="Exclude runs shorter than this distance (default: 0; include all positive distances)",
    )
    parser.add_argument(
        "--merge-gap-minutes", type=float, default=10.0,
        help="Merge same-day/source runs with a short segment and gaps up to this many minutes (0 disables)",
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
    if not math.isfinite(args.merge_gap_minutes) or args.merge_gap_minutes < 0:
        raise SystemExit("--merge-gap-minutes must be a finite nonnegative number")
    if args.health_zip is not None:
        workouts = read_workouts_from_zip(args.health_zip, args.min_distance_km)
    else:
        workouts = read_workouts(args.db, args.min_distance_km)
    if not workouts:
        raise SystemExit("No valid running workouts were found; output was not changed.")
    activities, route_count = build_activities(
        workouts,
        routes_zip=args.routes_zip or args.health_zip,
        trim_meters=args.trim_meters,
        max_route_points=args.max_route_points,
        merge_gap_minutes=args.merge_gap_minutes,
    )
    recording_count = sum(len(item.get("segments", [item])) for item in activities)
    public_distance = sum(item["distance_km"] for item in activities)
    if recording_count != len(workouts) or not math.isclose(
        public_distance, sum(workout.distance_km for workout in workouts),
        rel_tol=0, abs_tol=max(1, len(workouts)) * 0.000001,
    ):
        raise RuntimeError("Session merging changed the original workout count or distance; output was not changed.")

    output_path = args.output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            build_payload(activities, args.trim_meters, args.min_distance_km, args.merge_gap_minutes),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    total_distance = sum(float(item["distance_km"]) for item in activities)
    heart_rate_count = sum(item["avg_hr_bpm"] is not None for item in activities)
    merged = [item for item in activities if "segments" in item]
    if merged:
        print(f"Merged {sum(len(item['segments']) for item in merged)} recordings into {len(merged)} running sessions.")
    print(
        f"Exported {len(activities)} runs / {total_distance:.1f} km / "
        f"{heart_rate_count} heart-rate summaries / {route_count} privacy-trimmed routes "
        f"to {output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
