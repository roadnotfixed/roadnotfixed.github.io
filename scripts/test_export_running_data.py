import sqlite3
import tempfile
import unittest
import zipfile
from datetime import timedelta, timezone
from pathlib import Path

from export_running_data import Workout, build_activities, group_workouts, parse_datetime, read_workouts, read_workouts_from_zip


class ExportZipTests(unittest.TestCase):
    def read(self, body, minimum=2.0):
        with tempfile.TemporaryDirectory() as directory:
            export_zip = Path(directory) / "export.zip"
            with zipfile.ZipFile(export_zip, "w") as archive:
                archive.writestr("apple_health_export/export.xml", "<HealthData>" + body + "</HealthData>")
            return read_workouts_from_zip(export_zip, minimum)

    def test_utc_identity_merges_offset_and_device_variants(self):
        template = (
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" '
            'device="{device}" startDate="{start}" endDate="{end}" '
            'duration="30" durationUnit="min" totalDistance="{distance}" totalDistanceUnit="km"/>'
        )
        workouts = self.read(
            template.format(device="0x111", start="2026-08-08 18:07:35 -0700", end="2026-08-08 18:37:35 -0700", distance="5")
            + template.format(device="0x222", start="2026-08-09 11:07:35 +1000", end="2026-08-09 11:37:35 +1000", distance="5.1")
        )
        self.assertEqual(len(workouts), 1)
        self.assertEqual(workouts[0].start_utc.isoformat(), "2026-08-09T01:07:35+00:00")
        self.assertEqual(workouts[0].distance_km, 5.1)

    def test_nested_statistics_survive_skipping_health_records(self):
        workouts = self.read(
            '<Record type="HKQuantityTypeIdentifierStepCount" value="100"><MetadataEntry key="x" value="y"/></Record>'
            '<Correlation><Record type="HKQuantityTypeIdentifierHeartRate" value="190"/></Correlation>'
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" '
            'startDate="2026-10-30 08:00:00 -0700" endDate="2026-10-30 09:00:00 -0700" duration="3600" durationUnit="s">'
            '<WorkoutStatistics type="HKQuantityTypeIdentifierDistanceWalkingRunning" sum="5" unit="mi"/>'
            '<WorkoutStatistics type="HKQuantityTypeIdentifierHeartRate" average="150" unit="count/min"/>'
            '</Workout>'
        )
        self.assertEqual(len(workouts), 1)
        self.assertAlmostEqual(workouts[0].distance_km, 8.04672)
        self.assertEqual(workouts[0].duration_min, 60.0)
        self.assertEqual(workouts[0].avg_hr_bpm, 150)

    def test_distinct_runs_stay_separate_and_invalid_or_short_runs_are_skipped(self):
        def workout(kind, start, end, distance, duration="30"):
            return (
                f'<Workout workoutActivityType="{kind}" sourceName="Watch" startDate="{start}" '
                f'endDate="{end}" duration="{duration}" totalDistance="{distance}" totalDistanceUnit="km"/>'
            )

        running = "HKWorkoutActivityTypeRunning"
        body = workout(running, "2026-10-30 08:00:00 -0700", "2026-10-30 08:30:00 -0700", "5")
        body += workout(running, "2026-10-30 09:00:00 -0700", "2026-10-30 09:30:00 -0700", "5")
        for kind, distance, duration in [(running, "1.99", "30"), (running, "nan", "30"), (running, "5", "0"), ("HKWorkoutActivityTypeWalking", "5", "30")]:
            body += workout(kind, "2026-10-30 10:00:00 -0700", "2026-10-30 10:30:00 -0700", distance, duration)
        self.assertEqual(len(self.read(body)), 2)

    def test_all_runs_mode_retains_short_positive_distances(self):
        template = (
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" '
            'startDate="2026-10-30 08:00:00 -0700" endDate="2026-10-30 08:01:00 -0700" '
            'duration="1" totalDistance="{distance}" totalDistanceUnit="km"/>'
        )
        workouts = self.read(template.format(distance="0.0247254") + template.format(distance="0"), minimum=0)
        self.assertEqual(len(workouts), 1)
        self.assertAlmostEqual(workouts[0].distance_km, 0.0247254)

    def test_statistics_begin_in_2022_by_local_workout_date(self):
        dates = [
            ("2020-07-01 10:00:00 -0700", "2020-07-01 10:30:00 -0700"),
            ("2021-12-31 23:30:00 -1200", "2022-01-01 00:00:00 -1200"),
            ("2022-01-01 00:00:00 +1400", "2022-01-01 00:30:00 +1400"),
            ("2022-01-02 10:00:00 -0700", "2022-01-02 10:30:00 -0700"),
        ]
        body = "".join(
            '<Workout workoutActivityType="HKWorkoutActivityTypeRunning" sourceName="Watch" '
            f'startDate="{start}" endDate="{end}" duration="30" totalDistance="5" totalDistanceUnit="km"/>'
            for start, end in dates
        )
        workouts = self.read(body, minimum=0)
        self.assertEqual([w.date for w in workouts], ["2022-01-01", "2022-01-02"])
        self.assertEqual(workouts[0].start_utc.year, 2021)

    def test_database_export_uses_the_same_2022_cutoff(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "health.db"
            with sqlite3.connect(str(db)) as connection:
                connection.executescript(
                    "CREATE TABLE workouts(workout_hash, start_date, end_date, duration_min, total_distance_km, workout_type, source_name);"
                    "CREATE TABLE workout_stats(workout_hash, type, avg_value);"
                )
                connection.executemany(
                    "INSERT INTO workouts VALUES (?, ?, ?, 30, 5, 'HKWorkoutActivityTypeRunning', 'Watch')",
                    [
                        ("old", "2021-12-31T23:00:00-12:00", "2021-12-31T23:30:00-12:00"),
                        ("new", "2022-01-01T00:00:00+14:00", "2022-01-01T00:30:00+14:00"),
                    ],
                )
            connection.close()
            self.assertEqual([w.workout_hash for w in read_workouts(db, 0)], ["new"])


class RunningSessionTests(unittest.TestCase):
    def workout(self, name, start, duration, distance, heart_rate=None, source="Watch"):
        parsed = parse_datetime(start)
        utc = parsed.astimezone(timezone.utc)
        return Workout(name, parsed.isoformat(), utc, utc + timedelta(minutes=duration),
                       parsed.date().isoformat(), duration, distance, heart_rate, source)

    def test_short_three_segment_chain_merges_and_preserves_distance_and_active_time(self):
        workouts = [
            self.workout("a", "2026-08-25T14:44:00-07:00", 8, 1.1, 120),
            self.workout("b", "2026-08-25T14:53:00-07:00", 6, 1.0, 160),
            self.workout("c", "2026-08-25T15:03:00-07:00", 30, 5.0),
        ]
        activities, count = build_activities(workouts, None, 500, 120)
        self.assertEqual(len(activities), 1)
        self.assertAlmostEqual(activities[0]["distance_km"], 7.1)
        self.assertEqual(activities[0]["duration_min"], 44)
        self.assertEqual(activities[0]["avg_hr_bpm"], 137)
        self.assertEqual(activities[0]["pace_min_km"], round(44 / 7.1, 2))
        self.assertEqual(len(activities[0]["segments"]), 3)
        self.assertEqual(count, 0)

    def test_exact_ten_minutes_merges_using_utc(self):
        a = self.workout("a", "2026-08-09T01:00:00+00:00", 5, 0.5)
        b = self.workout("b", "2026-08-09T11:15:00+10:00", 30, 5)
        self.assertEqual([len(group) for group in group_workouts([b, a])], [2])

    def test_beyond_ten_minutes_overlap_other_source_and_other_day_stay_separate(self):
        cases = [
            ("2026-08-25T10:15:01+00:00", "Watch"),
            ("2026-08-25T10:04:00+00:00", "Watch"),
            ("2026-08-25T10:06:00+00:00", "Other app"),
        ]
        a = self.workout("a", "2026-08-25T10:00:00+00:00", 5, 0.5)
        for start, source in cases:
            with self.subTest(start=start, source=source):
                b = self.workout("b", start, 30, 5, source=source)
                self.assertEqual(len(group_workouts([a, b])), 2)
        a = self.workout("a", "2026-12-31T23:50:00+00:00", 9, 0.5)
        b = self.workout("b", "2027-01-01T00:00:00+00:00", 30, 5)
        self.assertEqual(len(group_workouts([a, b])), 2)

    def test_long_only_runs_stay_separate_and_merge_can_be_disabled(self):
        a = self.workout("a", "2026-08-25T10:00:00+00:00", 30, 5)
        b = self.workout("b", "2026-08-25T10:31:00+00:00", 30, 5)
        self.assertEqual(len(group_workouts([a, b])), 2)
        a.distance_km = 1
        self.assertEqual(len(group_workouts([a, b], max_gap_minutes=0)), 2)

    def test_merged_routes_remain_separate_and_privacy_trimmed(self):
        workouts = [
            self.workout("a", "2026-08-25T10:00:00+00:00", 5, 0.5),
            self.workout("b", "2026-08-25T10:06:00+00:00", 30, 5),
        ]
        with tempfile.TemporaryDirectory() as directory:
            route_zip = Path(directory) / "routes.zip"
            with zipfile.ZipFile(route_zip, "w") as archive:
                for workout in workouts:
                    points = "".join(
                        '<trkpt lat="{lat}" lon="-123"><time>{time}</time></trkpt>'.format(
                            lat=45 + i * 0.001,
                            time=(workout.start_utc + timedelta(minutes=i)).isoformat(),
                        ) for i in range(5)
                    )
                    archive.writestr(workout.workout_hash + ".gpx", "<gpx><metadata><time>" + workout.start_utc.isoformat()
                                     + "</time></metadata><trk><trkseg>" + points + "</trkseg></trk></gpx>")
            activities, count = build_activities(workouts, route_zip, 500, 120)
        self.assertEqual(count, 1)
        self.assertNotIn("route", activities[0])
        self.assertEqual(len(activities[0]["route_segments"]), 2)
        for route in activities[0]["route_segments"]:
            self.assertEqual(route, [[45.001, -123.0], [45.002, -123.0], [45.003, -123.0]])


if __name__ == "__main__":
    unittest.main()
