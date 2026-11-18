#!/usr/bin/env python3
"""Build data/papers.json for the Exoplanet Atlas from NASA ADS.

Pipeline
  1. Seeds:    papers matching the queries in config.json.
  2. Backward: papers the seeds cite (kept if cited by >= min_seed_support seeds).
  3. Forward:  papers that cite the seeds (most cited first).
  4. Graph:    citation edges between all collected papers.
  5. Subfields: Louvain communities on the citation graph.
  6. Layout:   subfield centres from the subfield-level citation graph; positions
               inside a subfield from a 2-D projection of title+abstract text.
  7. Write data/papers.json in the format the viewer expects.

Usage
  export ADS_API_TOKEN="your-token"          # never commit this
  python scripts/build_atlas.py --seeds-only # check your queries first
  python scripts/build_atlas.py              # full build

Responses are cached in data/raw/ (git-ignored), so re-runs are fast and free.
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

API = "https://api.adsabs.harvard.edu/v1"
FIELDS = "bibcode,title,year,citation_count,reference,abstract"


# --------------------------------------------------------------------------
# ADS client (only part of this file that talks to the network)
# --------------------------------------------------------------------------
class ADSClient:
    def __init__(self, token, cache_dir):
        import requests

        self.requests = requests
        self.session = requests.Session()
        self.session.headers["Authorization"] = "Bearer " + token
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)

    def _cache_path(self, *parts):
        key = hashlib.sha1(json.dumps(parts, sort_keys=True).encode()).hexdigest()
        return self.cache / (key + ".json")

    def _request(self, method, url, **kw):
        for attempt in range(6):
            r = self.session.request(method, url, timeout=120, **kw)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 401:
                sys.exit("ADS rejected the token (401). Check ADS_API_TOKEN.")
            if r.status_code in (429, 500, 502, 503, 504):
                wait = int(r.headers.get("Retry-After", 5 * 2 ** attempt))
                print(f"  ADS returned {r.status_code}; waiting {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            sys.exit(f"ADS error {r.status_code}: {r.text[:300]}")
        sys.exit("ADS kept failing after several retries.")

    def search(self, q, fl=FIELDS, rows=500, sort="citation_count desc", max_n=5000):
        """Return up to max_n documents matching q."""
        path = self._cache_path("search", q, fl, sort, max_n)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        docs, start = [], 0
        while len(docs) < max_n:
            n = min(rows, max_n - len(docs))
            data = self._request(
                "GET", API + "/search/query",
                params={"q": q, "fl": fl, "rows": n, "start": start, "sort": sort},
            )
            batch = data["response"]["docs"]
            docs.extend(batch)
            total = data["response"]["numFound"]
            start += len(batch)
            print(f"  fetched {len(docs)} of {min(total, max_n)}")
            if not batch or start >= total:
                break
        path.write_text(json.dumps(docs), encoding="utf-8")
        return docs

    def bigquery(self, bibcodes, fl=FIELDS):
        """Fetch metadata for a list of bibcodes, in batches."""
        out = []
        for i in range(0, len(bibcodes), 1000):
            chunk = bibcodes[i:i + 1000]
            path = self._cache_path("bigquery", chunk, fl)
            if path.exists():
                out.extend(json.loads(path.read_text(encoding="utf-8")))
                continue
            data = self._request(
                "POST", API + "/search/bigquery",
                params={"q": "*:*", "fq": "{!bitset}", "fl": fl, "rows": len(chunk)},
                data="bibcode\n" + "\n".join(chunk),
                headers={"Content-Type": "big-query/csv"},
            )
            docs = data["response"]["docs"]
            path.write_text(json.dumps(docs), encoding="utf-8")
            out.extend(docs)
            print(f"  fetched metadata for {min(i + 1000, len(bibcodes))} of {len(bibcodes)} bibcodes")
        return out


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def clean(doc):
    title = doc.get("title") or ""
    if isinstance(title, list):
        title = title[0] if title else ""
    try:
        year = int(doc.get("year"))
    except (TypeError, ValueError):
        return None
    if not doc.get("bibcode") or not title:
        return None
    return {
        "id": doc["bibcode"],
        "title": title.strip(),
        "year": year,
        "cites": int(doc.get("citation_count") or 0),
        "refs": list(doc.get("reference") or []),
        "abstract": doc.get("abstract") or "",
    }


def seed_query(cfg, lo=None, hi=None):
    lo = lo or cfg["year_min"]
    hi = hi or cfg["year_max"]
    joined = " OR ".join("(" + q + ")" for q in cfg["seed_queries"])
    return f"({joined}) AND collection:astronomy AND year:{lo}-{hi}"


def fetch_seeds(client, cfg):
    """Seed papers, taking the most cited in each year slice so recent papers are not crowded out."""
    slices = cfg.get("seed_year_slices") or [[cfg["year_min"], cfg["year_max"]]]
    per = cfg.get("max_seeds_per_slice", cfg["max_seeds"])
    seeds = {}
    for lo, hi in slices:
        print(f"  years {lo}-{hi}")
        for d in map(clean, client.search(seed_query(cfg, lo, hi), max_n=per)):
            if d:
                seeds.setdefault(d["id"], d)
    return list(seeds.values())


def collect(client, cfg):
    q = seed_query(cfg)
    print("Seeds ...")
    seeds = fetch_seeds(client, cfg)
    papers = {d["id"]: d for d in seeds}
    print(f"  {len(papers)} seed papers")

    print("Backward references ...")
    support = collections.Counter(r for p in seeds for r in p["refs"] if r not in papers)
    wanted = [b for b, c in support.items() if c >= cfg["min_seed_support"]]
    print(f"  {len(wanted)} papers cited by >= {cfg['min_seed_support']} seeds")
    back = [d for d in map(clean, client.bigquery(wanted)) if d and d["year"] >= cfg["backward_year_min"]]
    back.sort(key=lambda d: -d["cites"])
    for d in back[: cfg["max_backward"]]:
        papers.setdefault(d["id"], d)

    print("Forward citations ...")
    fq = f"citations({q}) AND collection:astronomy AND year:{cfg['year_min']}-{cfg['year_max']}"
    if cfg.get("forward_must_match", True):
        # keep only citing papers that are themselves on topic (stops big general surveys
        # that merely cite an exoplanet paper from flooding the map)
        topic = " OR ".join("(" + t + ")" for t in cfg["seed_queries"])
        fq += f" AND ({topic})"
    for d in map(clean, client.search(fq, max_n=cfg["max_forward"])):
        if d:
            papers.setdefault(d["id"], d)

    print(f"Total papers collected: {len(papers)}")
    return list(papers.values())


# --------------------------------------------------------------------------
# Graph, subfields, layout
# --------------------------------------------------------------------------
def build_graph(papers, drop_isolates):
    import networkx as nx

    # Insert nodes and edges in sorted order so community detection gives the same
    # result (and the same subfield ids) on every run, regardless of Python's hash seed.
    ids = {p["id"] for p in papers}
    g = nx.DiGraph()
    g.add_nodes_from(sorted(ids))
    for p in sorted(papers, key=lambda p: p["id"]):
        for r in sorted(set(p["refs"])):
            if r in ids and r != p["id"]:
                g.add_edge(p["id"], r)
    if drop_isolates:
        g.remove_nodes_from(sorted(n for n in g if g.degree(n) == 0))
    return g



def _split(u, nodes, resolution, max_children, min_child_size):
    """Split a set of nodes into communities. Returns [(suffix, members)] or None if there is no real split."""
    import networkx as nx

    # explicit copy in sorted order: subgraph views can iterate in set order, which varies between runs
    sub = nx.Graph()
    sub.add_nodes_from(sorted(nodes))
    sub.add_edges_from(sorted(tuple(sorted(e)) for e in u.subgraph(nodes).edges))
    comms = nx.community.louvain_communities(sub, resolution=resolution, seed=0)
    comms = sorted(comms, key=len, reverse=True)
    kept, other = [], set()
    for i, c in enumerate(comms):
        if i < max_children and len(c) >= min_child_size:
            kept.append(c)
        else:
            other |= set(c)
    if not kept or (len(kept) == 1 and not other):
        return None
    parts = [(str(i), set(c)) for i, c in enumerate(kept)]
    if other:
        parts.append(("other", other))
    return parts


def build_hierarchy(g, cfg):
    """Recursive Louvain. Returns (leaf_of_node, tree) where tree maps cluster id -> {"parent", "depth"}.

    Ids look like c0, c0.1, c0.1.2. Every paper belongs to exactly one leaf; a cluster's papers are all
    papers whose leaf id starts with its id.
    """
    import networkx as nx

    u = nx.Graph(g)
    max_depth = cfg.get("max_depth", 3)
    min_split = cfg.get("min_split_size", 150)
    max_children = cfg.get("max_children", cfg.get("max_clusters", 8))
    min_child = cfg.get("min_child_size", 20)
    res = cfg.get("resolution", 1.0)
    leaf_of, tree = {}, {}

    def rec(cid, nodes, depth):
        if depth < max_depth and len(nodes) >= min_split:
            parts = _split(u, nodes, res, max_children, min_child)
            if parts:
                for suffix, members in parts:
                    if depth == 0:
                        child = "c" + suffix if suffix != "other" else "other"
                    else:
                        child = cid + "." + suffix
                    tree[child] = {"parent": cid or None, "depth": depth + 1}
                    if suffix == "other":  # never split the catch-all
                        for n in members:
                            leaf_of[n] = child
                    else:
                        rec(child, members, depth + 1)
                return
        if not cid:  # whole graph is one community
            cid = "c0"
            tree[cid] = {"parent": None, "depth": 1}
        for n in nodes:
            leaf_of[n] = cid

    rec("", set(g.nodes), 0)
    return leaf_of, tree


def ancestors(cid):
    parts = cid.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def text_features(papers):
    from sklearn.feature_extraction.text import TfidfVectorizer

    docs = [(p["title"] + " ") * 3 + p["abstract"] for p in papers]
    vec = TfidfVectorizer(stop_words="english", min_df=3, max_df=0.5, ngram_range=(1, 2),
                          sublinear_tf=True, max_features=50000)
    try:
        x = vec.fit_transform(docs)
    except ValueError:  # too little text
        return None, []
    return x, vec.get_feature_names_out()




def name_clusters(tree, rows_of, x, terms, overrides):
    """Name each cluster by the words that distinguish it from its parent."""
    import numpy as np

    names = {}
    means = {}
    if x is not None and len(terms):
        means[None] = np.asarray(x.mean(axis=0)).ravel()
        for cid, rows in rows_of.items():
            means[cid] = np.asarray(x[rows].mean(axis=0)).ravel()
    for cid, info in tree.items():
        if cid in overrides:
            names[cid] = overrides[cid]
        elif cid == "other" or cid.endswith(".other"):
            names[cid] = "Other"
        elif cid in means:
            score = means[cid] - means[info["parent"]]
            names[cid] = ", ".join(terms[i] for i in score.argsort()[::-1][:3])
        else:
            names[cid] = cid
    return names


def _arrange(kids, sizes, weights):
    """Place child clusters inside a unit disc. Returns {cid: (cx, cy, r)} with centres in [-0.7, 0.7]."""
    import networkx as nx
    import numpy as np

    if len(kids) == 1:
        return {kids[0]: (0.0, 0.0, 0.92)}
    cg = nx.Graph()
    cg.add_nodes_from(kids)
    for (a, b), w in weights.items():
        cg.add_edge(a, b, weight=w)
    raw = nx.spring_layout(cg, weight="weight", seed=1, iterations=300)
    pts = np.array([raw[c] for c in kids], dtype=float)
    pts -= pts.mean(axis=0)
    top = np.linalg.norm(pts, axis=1).max()
    pts *= 0.7 / top if top > 1e-9 else 1.0
    biggest = max(sizes)
    out = {}
    for i, c in enumerate(kids):
        near = min(np.linalg.norm(pts[i] - pts[j]) for j in range(len(kids)) if j != i)
        r = max(0.08, min(0.42 * near, 0.12 + 0.3 * (sizes[i] / biggest) ** 0.5))
        r = max(0.05, min(r, 0.95 - float(np.linalg.norm(pts[i]))))
        out[c] = (float(pts[i][0]), float(pts[i][1]), float(r))
    return out


def layout(g, leaf_of, index, x, tree):
    """Nested layout: children are arranged inside their parent's disc; leaves use a text projection."""
    import numpy as np

    kids_of = collections.defaultdict(list)
    for cid, info in tree.items():
        kids_of[info["parent"]].append(cid)
    size = collections.Counter()
    leaf_rows = collections.defaultdict(list)
    for pid, leaf in sorted(leaf_of.items()):
        leaf_rows[leaf].append(index[pid])
        for a in ancestors(leaf):
            size[a] += 1
    for k in kids_of:
        kids_of[k].sort(key=lambda c: -size[c])

    # citation weight between sibling clusters, grouped by their parent
    weights = collections.defaultdict(lambda: collections.defaultdict(int))
    for u, v in g.edges:
        tu, tv = leaf_of[u].split("."), leaf_of[v].split(".")
        k = 0
        while k < min(len(tu), len(tv)) and tu[k] == tv[k]:
            k += 1
        if k < len(tu) and k < len(tv):
            parent = ".".join(tu[:k]) or None
            a, b = ".".join(tu[: k + 1]), ".".join(tv[: k + 1])
            weights[parent][tuple(sorted((a, b)))] += 1

    xy = {}

    def place_leaf(cid, cx, cy, radius):
        rows = leaf_rows[cid]
        ys = None
        if x is not None and len(rows) >= 4:
            try:
                from sklearn.decomposition import TruncatedSVD

                ys = TruncatedSVD(n_components=2, random_state=0).fit_transform(x[rows])
            except ValueError:
                ys = None
        if ys is None:
            ys = np.array([[np.cos(i * 2.4), np.sin(i * 2.4)] for i in range(len(rows))]) * 0.6
        scale = np.percentile(np.linalg.norm(ys, axis=1), 90) or 1.0
        ys = ys / scale
        ys = ys / np.maximum(1.0, np.linalg.norm(ys, axis=1))[:, None]
        for row, vec in zip(rows, ys):
            xy[row] = np.array([cx, cy]) + radius * 0.92 * vec

    def place(parent, cx, cy, radius):
        kids = kids_of.get(parent)
        arr = _arrange(kids, [size[c] for c in kids], weights.get(parent, {}))
        for c in kids:
            ox, oy, r = arr[c]
            ccx, ccy, cr = cx + radius * ox, cy + radius * oy, radius * r
            if kids_of.get(c):
                place(c, ccx, ccy, cr)
            else:
                place_leaf(c, ccx, ccy, cr)

    place(None, 0.5, 0.5, 0.47)
    return xy, size


def jitter(pid, amount):
    h = hashlib.md5(pid.encode()).digest()
    return ((h[0] / 255) - 0.5) * amount, ((h[1] / 255) - 0.5) * amount




def build(papers, cfg, overrides=None):
    g = build_graph(papers, cfg["drop_isolates"])
    keep = [p for p in papers if p["id"] in g]
    if not keep:
        sys.exit("No papers have citation links to each other. Loosen the seed queries.")
    index = {p["id"]: i for i, p in enumerate(keep)}
    leaf_of, tree = build_hierarchy(g, cfg)
    x, terms = text_features(keep)

    rows_of = collections.defaultdict(list)  # every cluster -> rows of all papers beneath it
    for pid, leaf in leaf_of.items():
        for a in ancestors(leaf):
            rows_of[a].append(index[pid])
    names = name_clusters(tree, rows_of, x, terms, overrides or {})
    xy, size = layout(g, leaf_of, index, x, tree)
    # stretch the whole layout to fill the unit square (same scale on both axes)
    allxy = [xy[index[p["id"]]] for p in keep]
    lo_x, hi_x = min(a[0] for a in allxy), max(a[0] for a in allxy)
    lo_z, hi_z = min(a[1] for a in allxy), max(a[1] for a in allxy)
    scale = 0.92 / max(hi_x - lo_x, hi_z - lo_z, 1e-9)
    off_x = 0.5 - scale * (lo_x + hi_x) / 2
    off_z = 0.5 - scale * (lo_z + hi_z) / 2
    xy = {i: (scale * v[0] + off_x, scale * v[1] + off_z) for i, v in xy.items()}

    out_papers = []
    for p in keep:
        i = index[p["id"]]
        jx, jz = jitter(p["id"], 0.006)
        out_papers.append({
            "id": p["id"],
            "title": p["title"],
            "year": p["year"],
            "x": round(min(1.0, max(0.0, float(xy[i][0]) + jx)), 4),
            "z": round(min(1.0, max(0.0, float(xy[i][1]) + jz)), 4),
            "cluster": leaf_of[p["id"]],
            "cites": p["cites"],
            "refs": [r for r in p["refs"] if r in index],
            "url": p.get("url") or "https://ui.adsabs.harvard.edu/abs/" + quote(p["id"], safe="") + "/abstract",
        })

    kids_of = collections.defaultdict(list)
    for cid, info in tree.items():
        kids_of[info["parent"]].append(cid)
    clusters = []

    def walk(parent):
        for c in sorted(kids_of.get(parent, []), key=lambda c: -size[c]):
            clusters.append({"id": c, "name": names[c], "parent": parent, "depth": tree[c]["depth"], "n": size[c]})
            walk(c)

    walk(None)
    meta = {
        "generated": datetime.date.today().isoformat(),
        "source": cfg.get("source", "NASA ADS"),
        "papers": len(out_papers),
        "seed_queries": cfg["seed_queries"],
    }
    return {"meta": meta, "clusters": clusters, "papers": out_papers}, rows_of, names


def main(argv=None, client=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(here / "config.json"))
    ap.add_argument("--names", default=str(here / "cluster_names.json"),
                    help="optional JSON mapping cluster id (c0, c1, ...) to a display name")
    ap.add_argument("--out", default=str(here.parent / "data" / "papers.json"))
    ap.add_argument("--cache", default=str(here.parent / "data" / "raw"))
    ap.add_argument("--seeds-only", action="store_true", help="only run the seed query and print a preview")
    args = ap.parse_args(argv)

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if client is None:
        token = os.environ.get("ADS_API_TOKEN")
        if not token:
            sys.exit("Set your ADS token first:  export ADS_API_TOKEN='...'  (get one at ui.adsabs.harvard.edu)")
        client = ADSClient(token, args.cache)

    if args.seeds_only:
        docs = fetch_seeds(client, cfg)
        print(f"\n{len(docs)} seed papers. Most cited:")
        for d in sorted(docs, key=lambda d: -d["cites"])[:15]:
            print(f"  {d['year']}  {d['cites']:>5}  {d['title'][:90]}")
        print("\nIf these look off-topic or too few, edit seed_queries in config.json and rerun.")
        return 0

    papers = collect(client, cfg)
    overrides = {}
    if Path(args.names).exists():
        overrides = json.loads(Path(args.names).read_text(encoding="utf-8"))
    result, members, names = build(papers, cfg, overrides)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"\nWrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(result['papers'])} papers)")
    print("\nSubfields found, indented by level (rename any in scripts/cluster_names.json, then rerun):")
    for c in result["clusters"]:
        print(f"  {'  ' * (c['depth'] - 1)}{c['id']}  ({c['n']} papers)  {c['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
