# Narrenschiff

Source for <https://roadnotfixed.github.io/>.

The Hugo site lives in `oldifnotwild-blog/`. Pushes to `main` are built and
deployed to GitHub Pages by the workflow in `.github/workflows/hugo.yml`.

## Running data

The Running page uses a privacy-filtered JSON export generated from a local
Apple Health SQLite database. The export contains only the local calendar date,
distance, duration, and calculated pace for each run. It does not include
routes, coordinates, exact start times, heart rate, device details, source
accounts, API credentials, or activity identifiers.

Refresh the public data before publishing new runs:

```text
python scripts/export_running_data.py --db path/to/health.db
```
