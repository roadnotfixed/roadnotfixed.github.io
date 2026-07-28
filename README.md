# Narrenschiff

Source for <https://roadnotfixed.github.io/>.

The Hugo site is built and deployed to GitHub Pages by the workflow in `.github/workflows/hugo.yml`.

## Running data

The Running page uses a privacy-filtered JSON export generated from a local
Apple Health SQLite database and its original workout-route archive. It includes
the local calendar date, distance, duration, calculated pace, average heart
rate when available, and simplified route lines. Route timestamps and elevation
are removed, coordinates are rounded and downsampled, and up to 500 metres are
trimmed from both endpoints. It does not include exact start times, device
details, source accounts, API credentials, or activity identifiers. Runs shorter
than 2 km are excluded from the public page.

Refresh the public data before publishing new runs:

```text
python scripts/export_running_data.py --db path/to/health.db --routes-zip path/to/watch_data.zip
```

## Photo albums

Photo albums are Hugo page bundles under `roadnotfixed-blog/content/photos/`.
From the `roadnotfixed-blog` directory, create an album with:

```text
hugo new content --kind photos photos/my-album/index.md
```

Place the original JPEG, PNG, or WebP files beside `index.md`. Set `cover` in
the album front matter to the preferred cover filename, and set `featured` to
`true` to show the album in the large featured position. Hugo creates the
responsive WebP variants during the site build; generated images should not be
committed separately.

Optional per-photo titles and alt text can be added to the album front matter:

```toml
[[resources]]
src = "01.jpg"
title = "A short visible caption"

[resources.params]
alt = "A concrete description of the photograph"
```

Preview draft albums with `hugo server -D`. Remove `draft = true` or set it to
`false` when the album is ready to publish.

## Links and RSS

Edit `roadnotfixed-blog/content/links.md` to add or update friend links. Keep
each entry in the simple Markdown form `[Site name](https://example.com/) - A
short description.`

The article RSS feed is generated automatically at `/blog/index.xml`. The site
advertises this feed in every page's HTML metadata and links to it from the
footer; no separate feed file needs to be maintained.
