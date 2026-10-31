import tempfile
import unittest
import zipfile
from pathlib import Path

from export_running_data import read_workouts_from_zip


class ExportZipTests(unittest.TestCase):
    def read(self, body):
        with tempfile.TemporaryDirectory() as directory:
            export_zip = Path(directory) / "export.zip"
            with zipfile.ZipFile(export_zip, "w") as archive:
                archive.writestr("apple_health_export/export.xml", "<HealthData>" + body + "</HealthData>")
            return read_workouts_from_zip(export_zip, 2.0)

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


if __name__ == "__main__":
    unittest.main()
