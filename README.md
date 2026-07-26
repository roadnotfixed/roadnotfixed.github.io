# Narrenschiff

Source for <https://roadnotfixed.github.io/>.

The Hugo site lives in `oldifnotwild-blog/`. Pushes to `main` are built and
deployed to GitHub Pages by the workflow in `.github/workflows/hugo.yml`.

## Running data

The Running page uses a privacy-filtered JSON export generated from a local
Apple Health SQLite database and its original workout-route archive. It includes
the local calendar date, distance, duration, calculated pace, average heart
rate when available, and simplified route lines. Route timestamps and elevation
are removed, coordinates are rounded and downsampled, and up to 500 metres are
trimmed from both endpoints. It does not include exact start times, device
details, source accounts, API credentials, or activity identifiers.

Refresh the public data before publishing new runs:

```text
python scripts/export_running_data.py --db path/to/health.db --routes-zip path/to/watch_data.zip
```
