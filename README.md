# Exoplanet Atlas

An interactive 3D map of the exoplanet literature. Each dot is a paper: height is time, the horizontal plane groups papers by subfield into columns, and dot size reflects citations. Drag to rotate, scroll to zoom, right-drag to pan. Search titles and authors, choose a range of years (from and to, or play the years), colour by subfield or year, toggle subfield names and slow rotation, and click a paper to see its links to other papers in the atlas.

Live site: https://adg97astro.github.io/exoplanet-atlas/

## Status

The site currently shows **synthetic demo data** until `data/papers.json` exists. Nothing in the demo is a real paper.

## Data format

Put one file at `data/papers.json`:

```json
{
  "clusters": [
    { "id": "c0", "name": "Detection methods", "parent": null },
    { "id": "c0.0", "name": "Transit surveys", "parent": "c0" },
    { "id": "c0.0.1", "name": "TESS", "parent": "c0.0" }
  ],
  "papers": [
    {
      "id": "2018PASP..130f4502S",
      "title": "Paper title",
      "year": 2018,
      "x": 0.31,
      "z": 0.62,
      "cluster": "c0.0.1",
      "cites": 120,
      "refs": ["other-paper-id"],
      "url": "https://arxiv.org/abs/XXXX.XXXXX"
    }
  ]
}
```

- `x` and `z` are numbers from 0 to 1 (the horizontal layout, precomputed from your citation graph or embeddings). If `z` is missing, papers are placed at 0.5.
- Optional: `"flag": true` and `"flagNote": "short reason"` mark papers for review. A "Highlight flagged papers" toggle appears when any paper has a flag.
- Subfields can be nested to any depth with `parent` (`null` for top level). Each paper's `cluster` is its own deepest subfield. Data without `parent` works as a flat list.
- `refs` lists ids of papers this one cites. Ids that are not in the file are ignored.
- `url` must start with `https://`.
- `year` (a whole number) sets the height, so no separate y value is needed.
- The 3D library (three.js r128) loads from the cdnjs CDN, so visitors need an internet connection.

## Publish on GitHub Pages

1. Create a **public** repository named `exoplanet-atlas` on your account.
2. Upload everything in this folder (keep `index.html` in the repo root).
3. Go to **Settings > Pages**, set Source to **Deploy from a branch**, choose `main` and `/ (root)`, and save.
4. After a minute or two the site is live at the URL above. Each push redeploys it.

## Notes

- Do not commit API keys (for example an ADS token). `.gitignore` already excludes common key and raw-data paths.
- Findings about specific papers should be labelled as machine-flagged and unverified until a person has checked them.

## Build the real data (NASA ADS)

You need Python 3.9+ and a free ADS API token (sign in at https://ui.adsabs.harvard.edu, then Account > Settings > API Token).

```bash
pip install -r scripts/requirements.txt
export ADS_API_TOKEN="paste-your-token"     # Windows PowerShell: $env:ADS_API_TOKEN="..."
python scripts/build_atlas.py --seeds-only   # preview: are these the right papers?
python scripts/build_atlas.py                # full build, writes data/papers.json
```

1. Edit `seed_queries` in `scripts/config.json` until the `--seeds-only` preview looks right.
2. The full build collects the seeds, the papers they cite, and the papers that cite them, then finds subfields (Louvain communities on the citation graph) and lays them out.
3. Subfields get automatic names from their most distinctive words. To rename them, create `scripts/cluster_names.json`, for example `{"c0": "Transit vetting", "c1": "Atmospheric retrieval"}`, and rerun. Reruns use the cache in `data/raw/`, so they are fast.
4. Commit `data/papers.json` and push. Do not commit `data/raw/` or your token (both are git-ignored).

Layout: subfield columns are placed by how much they cite each other, and papers inside a column are positioned by similarity of title and abstract text.

### Alternative: build from OpenAlex instead of ADS

OpenAlex needs a free account and API key (required since February 2026): create an account at https://openalex.org and copy your key from https://openalex.org/settings/api.

```bash
pip install -r scripts/requirements.txt
export OPENALEX_API_KEY="paste-your-key"
export OPENALEX_EMAIL="you@example.com"            # optional
python scripts/build_atlas_openalex.py --seeds-only  # preview the seed papers
python scripts/build_atlas_openalex.py               # full build, writes data/papers.json
```

Seed queries live in `scripts/config_openalex.json` (use AND / OR and quotes; no commas). OpenAlex covers all fields, so keep "exoplanet" in each query to avoid unrelated papers. The output format is identical, so the viewer works with either source.

## Subfields and sub-subfields

The build finds nested subfields by running community detection recursively: first on the whole citation graph, then inside each large subfield, up to `max_depth` levels. In `config.json`:

- `max_depth` (default 3): how many levels deep to split.
- `min_split_size` (default 150): a subfield is only split if it has at least this many papers.
- `max_children` (default 8): the most subfields kept at each split; the rest are grouped as "Other".
- `min_child_size` (default 20): smaller groups are folded into "Other".
- `resolution` (default 1.0): higher gives more, smaller groups.

Names come from the words that most distinguish a subfield from its parent. To rename one, add its id to `scripts/cluster_names.json`, for example `{"c0": "Detection methods", "c0.1": "Radial velocity"}`, and rerun.

The viewer opens on the broad overview: top-level subfields only, list collapsed. Sub-subfields appear only when you ask for them: click a subfield's name on the map to look inside it, use the arrows in the Subfields list to expand it, or choose a finer **Detail level**. In the list, click a name to hide or show it (with everything inside), and "only" to isolate it. **Overview** goes back to the starting view.

## Coverage: most cited vs all

`scripts/config.json` now fetches all exoplanet papers in ADS (about 50,000), using a broad query (exoplanet, extrasolar planet, hot Jupiter, transiting planet, planet candidate, super-Earth, sub-Neptune) in year slices, plus the background papers they cite most. The viewer's **Papers** control has two tabs. **Most cited** shows the top-cited share of each publication year's papers, set with a 1 to 100% slider, so recent work is included at every setting; big maps open here at roughly 6,000 papers. **All papers** shows everything.

The earlier, smaller setup (about 5,000 papers) is kept as `scripts/config_small.json`:

```bash
python scripts/build_atlas.py --config scripts/config_small.json
```

The full build makes many more ADS requests the first time (expect 20 to 60 minutes) and needs a few GB of memory for the clustering; reruns use the cache.

## About panel (subfield stats)

The sidebar's **About** panel describes whatever you are looking at: the whole field, or the subfield you clicked into. It shows papers per year, the most-cited papers (click one to open it), the subfields it is most connected to through citations (click to go there), the most frequent authors and, for the top two levels, the leading first-author institutions. Author names are matched by surname and initials and institutions are parsed from affiliations, so both are approximate.

Authors and institutions come from ADS and are added at the end of a full build. To add them to an existing `data/papers.json` without re-clustering:

```bash
python scripts/build_atlas.py --people-only
```

Each paper also stores its first 20 authors (names in one shared table, `authors`, with `au` holding indices and `na` the full count when there are more). The search box matches titles and author names ("Winn", "Winn, J." or "Joshua Winn"), and author names in the paper details and About panel are clickable.
