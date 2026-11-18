#!/usr/bin/env python3
"""Build data/papers.json for the Exoplanet Atlas from OpenAlex.

Same pipeline and output format as build_atlas.py (the ADS version); only the
data source differs. Needs a free OpenAlex API key (OpenAlex has required one
since February 2026):

  1. Create a free account at https://openalex.org
  2. Copy your key from https://openalex.org/settings/api

Usage
  export OPENALEX_API_KEY="your-key"           # never commit this
  export OPENALEX_EMAIL="you@example.com"      # optional, identifies you politely
  python scripts/build_atlas_openalex.py --seeds-only   # check your queries first
  python scripts/build_atlas_openalex.py                # full build

Responses are cached in data/raw/ (git-ignored), so re-runs are fast and cheap.
"""
import argparse
import collections
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import build_atlas as core

API = "https://api.openalex.org"
SELECT = "id,title,publication_year,cited_by_count,referenced_works,abstract_inverted_index,doi"
PAGE = 100


def short(openalex_id):
    return openalex_id.rsplit("/", 1)[-1] if openalex_id else ""


def abstract_text(inverted):
    if not inverted:
        return ""
    words = {}
    for word, places in inverted.items():
        for i in places:
            words[i] = word
    return " ".join(words[i] for i in sorted(words))


def clean(work):
    """Convert an OpenAlex work into the dict the shared pipeline expects."""
    wid = short(work.get("id"))
    title = (work.get("title") or "").strip()
    year = work.get("publication_year")
    if not wid or not title or not year:
        return None
    doi = work.get("doi")
    return {
        "id": wid,
        "title": title,
        "year": int(year),
        "cites": int(work.get("cited_by_count") or 0),
        "refs": [short(r) for r in (work.get("referenced_works") or [])],
        "abstract": abstract_text(work.get("abstract_inverted_index")),
        "url": doi if doi and doi.startswith("https://") else "https://openalex.org/" + wid,
    }


class OpenAlexClient:
    def __init__(self, api_key, mailto, cache_dir):
        import requests

        self.session = requests.Session()
        self.api_key = api_key
        self.mailto = mailto
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, *parts):
        key = hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        return self.cache / ("oa_" + key + ".json")

    def _get(self, path, params):
        params = dict(params)
        params["api_key"] = self.api_key
        if self.mailto:
            params["mailto"] = self.mailto
        for attempt in range(6):
            r = self.session.get(API + path, params=params, timeout=120)
            if r.status_code == 200:
                return r.json()
            if r.status_code in (401, 403):
                sys.exit(f"OpenAlex rejected the request ({r.status_code}). Check OPENALEX_API_KEY: {r.text[:200]}")
            if r.status_code == 400:
                sys.exit(f"OpenAlex could not parse the query (400): {r.text[:400]}\nCheck seed_queries and filter_extra in the config.")
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 5 * 2 ** attempt))
                print(f"  OpenAlex returned {r.status_code}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"OpenAlex error {r.status_code}: {r.text[:300]}")
        sys.exit("OpenAlex kept failing after several retries (daily free allowance used up?).")

    def works(self, filt, sort="cited_by_count:desc", max_n=5000):
        """Return up to max_n works matching a filter string."""
        path = self._cache_path("works", filt, sort, max_n)
        if path.exists():
            return json.loads(path.read_text())
        out, cursor = [], "*"
        while cursor and len(out) < max_n:
            data = self._get("/works", {"filter": filt, "sort": sort, "select": SELECT,
                                        "per-page": PAGE, "cursor": cursor})
            out.extend(data["results"])
            total = data["meta"].get("count", 0)
            cursor = data["meta"].get("next_cursor")
            print(f"  fetched {min(len(out), max_n)} of {min(total, max_n)}")
            if not data["results"]:
                break
        out = out[:max_n]
        path.write_text(json.dumps(out))
        return out

    def by_ids(self, ids):
        """Fetch works by short OpenAlex ids, 100 per request."""
        out = []
        for i in range(0, len(ids), PAGE):
            chunk = ids[i:i + PAGE]
            path = self._cache_path("ids", chunk)
            if path.exists():
                out.extend(json.loads(path.read_text()))
                continue
            data = self._get("/works", {"filter": "openalex:" + "|".join(chunk), "select": SELECT, "per-page": PAGE})
            path.write_text(json.dumps(data["results"]))
            out.extend(data["results"])
            print(f"  fetched metadata for {min(i + PAGE, len(ids))} of {len(ids)} works")
        return out


# --------------------------------------------------------------------------
def seed_filter(cfg):
    for q in cfg["seed_queries"]:
        if "," in q:
            sys.exit("Remove commas from seed_queries (they separate filters in OpenAlex). Use AND/OR instead.")
    expr = " OR ".join("(" + q + ")" for q in cfg["seed_queries"])
    parts = [f"title_and_abstract.search:({expr})", f"publication_year:{cfg['year_min']}-{cfg['year_max']}"]
    if cfg.get("filter_extra"):
        parts.append(cfg["filter_extra"])
    return ",".join(parts)


def collect(client, cfg):
    sf = seed_filter(cfg)
    print("Seeds ...")
    seeds = [d for d in map(clean, client.works(sf, max_n=cfg["max_seeds"])) if d]
    papers = {d["id"]: d for d in seeds}
    print(f"  {len(papers)} seed papers")

    print("Backward references ...")
    support = collections.Counter(r for p in seeds for r in p["refs"] if r not in papers)
    wanted = [b for b, c in support.items() if c >= cfg["min_seed_support"]]
    print(f"  {len(wanted)} works cited by >= {cfg['min_seed_support']} seeds")
    back = [d for d in map(clean, client.by_ids(wanted)) if d and d["year"] >= cfg["backward_year_min"]]
    back.sort(key=lambda d: -d["cites"])
    for d in back[: cfg["max_backward"]]:
        papers.setdefault(d["id"], d)

    print("Forward citations ...")
    top = [d["id"] for d in sorted(seeds, key=lambda d: -d["cites"])[: min(100, cfg["forward_from_top_seeds"])]]
    if top:
        ff = f"cites:{'|'.join(top)},publication_year:{cfg['year_min']}-{cfg['year_max']}"
        if cfg.get("filter_extra"):
            ff += "," + cfg["filter_extra"]
        for d in map(clean, client.works(ff, max_n=cfg["max_forward"])):
            if d:
                papers.setdefault(d["id"], d)

    print(f"Total papers collected: {len(papers)}")
    return list(papers.values())


def main(argv=None, client=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(here / "config_openalex.json"))
    ap.add_argument("--names", default=str(here / "cluster_names.json"),
                    help="optional JSON mapping cluster id (c0, c1, ...) to a display name")
    ap.add_argument("--out", default=str(here.parent / "data" / "papers.json"))
    ap.add_argument("--cache", default=str(here.parent / "data" / "raw"))
    ap.add_argument("--seeds-only", action="store_true", help="only run the seed query and print a preview")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text())
    if client is None:
        key = os.environ.get("OPENALEX_API_KEY")
        if not key:
            sys.exit("Set your OpenAlex key first:  export OPENALEX_API_KEY='...'\n"
                     "Create a free account at https://openalex.org and copy the key from https://openalex.org/settings/api")
        client = OpenAlexClient(key, os.environ.get("OPENALEX_EMAIL"), args.cache)

    if args.seeds_only:
        docs = [d for d in map(clean, client.works(seed_filter(cfg), max_n=cfg["max_seeds"])) if d]
        print(f"\n{len(docs)} seed papers. Most cited:")
        for d in sorted(docs, key=lambda d: -d["cites"])[:15]:
            print(f"  {d['year']}  {d['cites']:>5}  {d['title'][:90]}")
        print("\nIf these look off-topic or too few, edit seed_queries in config_openalex.json and rerun.")
        return 0

    papers = collect(client, cfg)
    overrides = json.loads(Path(args.names).read_text()) if Path(args.names).exists() else {}
    result, members, names = core.build(papers, cfg, overrides)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False))
    print(f"\nWrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(result['papers'])} papers)")
    print("\nSubfields found, indented by level (rename any in scripts/cluster_names.json, then rerun):")
    for c in result["clusters"]:
        print(f"  {'  ' * (c['depth'] - 1)}{c['id']}  ({c['n']} papers)  {c['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
