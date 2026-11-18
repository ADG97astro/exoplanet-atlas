# Exoplanet Atlas

An interactive 3D map of the exoplanet literature. Each dot is a paper: height is time, the horizontal plane groups papers by subfield into columns, and dot size reflects citations. Drag to rotate, scroll to zoom, right-drag to pan. Search titles, filter by year (or play the years), colour by subfield or year, toggle subfield names and slow rotation, and click a paper to see its links to other papers in the atlas.

Live site: https://adg97astro.github.io/exoplanet-atlas/

## Status

The site currently shows **synthetic demo data** until `data/papers.json` exists. Nothing in the demo is a real paper.

## Data format

Put one file at `data/papers.json`:

```json
{
  "clusters": [
    { "id": "transit", "name": "Transit detection and vetting" }
  ],
  "papers": [
    {
      "id": "2018PASP..130f4502S",
      "title": "Paper title",
      "year": 2018,
      "x": 0.31,
      "z": 0.62,
      "cluster": "transit",
      "cites": 120,
      "refs": ["other-paper-id"],
      "url": "https://arxiv.org/abs/XXXX.XXXXX"
    }
  ]
}
```

- `x` and `z` are numbers from 0 to 1 (the horizontal layout, precomputed from your citation graph or embeddings). If `z` is missing, papers are placed at 0.5.
- Optional: `"flag": true` and `"flagNote": "short reason"` mark papers for review. A "Highlight flagged papers" toggle appears when any paper has a flag.
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
