"""Seed the Academic Knowledge Tree with Wikipedia encyclopedic content.

Fetches canonical Wikipedia articles for nodes in `taxonomy.json`, caches raw
responses outside git (`%LOCALAPPDATA%\\BrahmaAI\\cache\\wikipedia`), formats
node `index.md` pages and companion `wikipedia.md` reference articles with
strict CC BY-SA 4.0 attribution and section truncation caps.

Usage:
    python tools/seed_academic_taxonomy.py --status
    python tools/seed_academic_taxonomy.py --limit 10
    python tools/seed_academic_taxonomy.py --node humanities/philosophy/epistemology
    python tools/seed_academic_taxonomy.py            # seed all canonical spine nodes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent import second_brain  # noqa: E402
from tools.taxonomy import load_taxonomy  # noqa: E402

USER_AGENT = "BrahmaTaxonomySeeder/0.1 (local research; contact: user@localhost)"
API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
MAX_ARTICLE_BYTES = 40_000
MAX_LEAD_CHARS = 1_500


def get_cache_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    cache_dir = Path(local_app_data) / "BrahmaAI" / "cache" / "wikipedia"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def fetch_wikipedia_batch(titles: List[str], cache_dir: Path, force: bool = False, offline: bool = False) -> Dict[str, Dict[str, Any]]:
    """Fetch Wikipedia extracts and pageprops in batches, using local cache when possible."""
    results: Dict[str, Dict[str, Any]] = {}
    needed: List[str] = []

    # 1. Check local cache first
    for title in titles:
        safe_name = re.sub(r"[^\w-]", "_", title).lower()
        title_cache_file = cache_dir / f"by_title_{safe_name}.json"
        if not force and title_cache_file.is_file():
            try:
                data = json.loads(title_cache_file.read_text(encoding="utf-8"))
                results[title] = data
                continue
            except Exception:
                pass
        needed.append(title)

    if not needed or offline:
        return results

    # 2. Query MediaWiki API in chunks of 20
    chunk_size = 20
    for i in range(0, len(needed), chunk_size):
        chunk = needed[i:i + chunk_size]
        titles_param = "|".join(chunk)
        params = {
            "action": "query",
            "titles": titles_param,
            "prop": "extracts|pageprops|info",
            "explaintext": "1",
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
        url = f"{API_ENDPOINT}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                pages = data.get("query", {}).get("pages", [])

                for page in pages:
                    orig_title = page.get("title", "")
                    page_id = page.get("pageid", 0)
                    revid = page.get("lastrevid", 0)
                    qid = page.get("pageprops", {}).get("wikibase_item", "")
                    extract = page.get("extract", "")

                    item = {
                        "title": orig_title,
                        "pageid": page_id,
                        "revid": revid,
                        "wikidata": qid,
                        "extract": extract,
                        "source_url": f"https://en.wikipedia.org/wiki/{urllib.parse.quote(orig_title.replace(' ', '_'))}",
                        "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }

                    # Cache by revision and title
                    if page_id and revid:
                        page_cache_dir = cache_dir / str(page_id)
                        page_cache_dir.mkdir(parents=True, exist_ok=True)
                        rev_file = page_cache_dir / f"{revid}.json"
                        rev_file.write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")

                    safe_name = re.sub(r"[^\w-]", "_", orig_title).lower()
                    (cache_dir / f"by_title_{safe_name}.json").write_text(json.dumps(item, indent=2, ensure_ascii=False), encoding="utf-8")

                    results[orig_title] = item

                    # Also map back to requested chunk title if redirected
                    for req_title in chunk:
                        if req_title.lower() == orig_title.lower() or _slugify(req_title) == _slugify(orig_title):
                            results[req_title] = item

            time.sleep(0.5)  # Respect rate limit
        except Exception as exc:
            print(f"Warning: Failed to fetch batch {chunk[:3]}: {exc}", file=sys.stderr)

    return results


def _slugify(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", str(text or "")).strip().lower()
    return re.sub(r"[-\s]+", "-", text)


def format_article_markdown(title: str, extract: str, max_bytes: int = MAX_ARTICLE_BYTES) -> Tuple[str, bool, List[str]]:
    """Convert Wikipedia plain extract into clean Markdown with section headers and byte bounds."""
    lines = extract.splitlines()
    out_lines: List[str] = []
    omitted_sections: List[str] = []
    truncated = False
    current_bytes = 0

    for line in lines:
        stripped = line.strip()
        # Headings: === Heading === -> ### Heading; == Heading == -> ## Heading
        if stripped.startswith("====") and stripped.endswith("===="):
            hname = stripped.strip("=").strip()
            line = f"#### {hname}"
        elif stripped.startswith("===") and stripped.endswith("==="):
            hname = stripped.strip("=").strip()
            line = f"### {hname}"
        elif stripped.startswith("==") and stripped.endswith("=="):
            hname = stripped.strip("=").strip()
            line = f"## {hname}"
            # Check truncation cap at top-level section boundaries
            if current_bytes > max_bytes:
                truncated = True
                omitted_sections.append(hname)
                continue

        if truncated:
            if stripped.startswith("==") and stripped.endswith("=="):
                omitted_sections.append(stripped.strip("=").strip())
            continue

        out_lines.append(line)
        current_bytes += len(line.encode("utf-8")) + 1

    return "\n".join(out_lines), truncated, omitted_sections


def seed_node(
    vault: Path,
    node: Dict[str, Any],
    article_data: Dict[str, Any],
    children: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Write canonical index.md and wikipedia.md for an academic node."""
    knowledge_dir = vault / "wiki" / "library" / "knowledge"
    node_dir = knowledge_dir / node["id"]
    node_dir.mkdir(parents=True, exist_ok=True)

    today = time.strftime("%Y-%m-%d")
    title = node["title"]
    node_id = node["id"]
    pid = node["parent"]
    scope = node.get("scope", f"The study of {title}.")

    extract = article_data.get("extract", "")
    revid = article_data.get("revid", 0)
    source_url = article_data.get("source_url", f"https://en.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}")
    wikidata = article_data.get("wikidata") or node.get("wikidata", "")

    # 1. Format companion wikipedia.md
    formatted_body, truncated, omitted = format_article_markdown(title, extract)

    wiki_file = node_dir / "wikipedia.md"
    wiki_lines = [
        "---",
        "type: ReferenceArticle",
        f"title: {json.dumps(f'{title} (Wikipedia)')}",
        f"domain: {json.dumps(node_id)}",
        f"source_url: {json.dumps(source_url)}",
        f"revision_id: {revid}",
        "license: CC-BY-SA-4.0",
        f"fetched_at: {json.dumps(article_data.get('fetched_at', today))}",
        f"truncated: {str(truncated).lower()}",
        "---",
        "",
        f"# {title}",
        "",
        formatted_body.strip(),
        "",
    ]
    if truncated and omitted:
        wiki_lines.extend([
            "## Omitted Sections",
            "",
            "*(Sections omitted due to size constraints. Consult the full article on Wikipedia.)*",
            "",
            "\n".join(f"- {sec}" for sec in omitted[:15]),
            "",
        ])
    wiki_lines.extend([
        "---",
        f"*Adapted from Wikipedia, \"{title}\", revision {revid}, CC BY-SA 4.0. Changes may have been made. License deed: https://creativecommons.org/licenses/by-sa/4.0/*",
        "",
    ])
    wiki_file.write_text("\n".join(wiki_lines), encoding="utf-8")

    # 2. Extract Lead paragraph for index.md
    lead = ""
    if extract:
        lead_match = extract.split("\n\n==")[0].strip()
        if len(lead_match) > MAX_LEAD_CHARS:
            lead = lead_match[:MAX_LEAD_CHARS].rsplit(" ", 1)[0] + "…"
        else:
            lead = lead_match
    if not lead:
        lead = scope

    # 3. Format Subfields table
    subfield_rows: List[str] = []
    for ch in children:
        ch_slug = ch["id"].split("/")[-1]
        ch_scope = ch.get("scope", "")
        # Count notes in child directory
        ch_dir = knowledge_dir / ch["id"]
        note_count = len(list(ch_dir.glob("*.md"))) - 2 if ch_dir.is_dir() else 0
        if note_count < 0:
            note_count = 0
        subfield_rows.append(f"| [[{ch_slug}/index|{ch['title']}]] | {ch_scope} | {note_count} |")

    # 4. Count notes in this node directory (excluding index.md and wikipedia.md)
    local_notes = [f for f in node_dir.glob("*.md") if f.name not in ("index.md", "wikipedia.md")]

    # 5. Format canonical index.md
    index_file = node_dir / "index.md"
    aliases = list(node.get("aliases", []))
    if title not in aliases:
        aliases.insert(0, title)

    index_lines = [
        "---",
        "type: ConceptIndex",
        f"id: {json.dumps(node_id)}",
        f"title: {json.dumps(title)}",
        f"aliases: {json.dumps(aliases)}",
        f"parent: {json.dumps(pid) if pid else 'null'}",
        f"layer: {json.dumps(node.get('layer', 'spine'))}",
        f"wikipedia: {json.dumps(title)}",
        f"wikidata: {json.dumps(wikidata)}",
        f"source_url: {json.dumps(source_url)}",
        f"revision_id: {revid}",
        f"fetched_at: {json.dumps(article_data.get('fetched_at', today))}",
        "license: CC-BY-SA-4.0",
        f"seed_status: {'exact' if revid else 'fallback'}",
        "generated_by: tools/seed_academic_taxonomy.py@0.1",
        "---",
        "",
        f"# {title}",
        "",
        lead,
        "",
    ]

    if subfield_rows:
        index_lines.extend([
            "## Subfields & Disciplines",
            "",
            "| Subfield | Scope | Notes |",
            "|:---|:---|:---:|",
            "\n".join(subfield_rows),
            "",
        ])

    if local_notes:
        index_lines.extend([
            "## Notes in this Field",
            "",
            "\n".join(f"- [[{n.name}|{n.stem.replace('-', ' ').title()}]]" for n in sorted(local_notes, key=lambda x: x.name)),
            "",
        ])

    index_lines.extend([
        "## Reference",
        "",
        f"- [[wikipedia|{title} (Full Wikipedia Reference Article)]]",
        "",
        "---",
        f"*Adapted from Wikipedia, \"{title}\", revision {revid}, CC BY-SA 4.0. Changes may have been made. License deed: https://creativecommons.org/licenses/by-sa/4.0/*",
        "",
    ])

    index_file.write_text("\n".join(index_lines), encoding="utf-8")

    return {
        "id": node_id,
        "title": title,
        "revid": revid,
        "bytes_wiki": len(wiki_file.read_text(encoding="utf-8")),
        "bytes_index": len(index_file.read_text(encoding="utf-8")),
        "subfields": len(subfield_rows),
        "notes": len(local_notes),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", action="store_true", help="Print seeding status across taxonomy nodes.")
    parser.add_argument("--limit", type=int, help="Seed at most N nodes.")
    parser.add_argument("--node", help="Seed a specific node ID (e.g. 'humanities/philosophy/epistemology').")
    parser.add_argument("--force", action="store_true", help="Force re-fetch from Wikipedia.")
    parser.add_argument("--offline", action="store_true", help="Only use cached responses, no network.")
    args = parser.parse_args(argv)

    vault = second_brain.vault_root()
    if not vault or not vault.is_dir():
        print(f"Error: Vault not found: {vault}", file=sys.stderr)
        return 1

    nodes = load_taxonomy(vault)
    knowledge_dir = vault / "wiki" / "library" / "knowledge"
    cache_dir = get_cache_dir()

    # If --status requested
    if args.status:
        total = len(nodes)
        seeded = 0
        with_article = 0
        for n in nodes:
            ndir = knowledge_dir / n["id"]
            if (ndir / "index.md").is_file():
                seeded += 1
            if (ndir / "wikipedia.md").is_file():
                with_article += 1

        print(
            f"Taxonomy Seeding Status:\n"
            f"  Total canonical nodes: {total}\n"
            f"  Nodes with index.md: {seeded} ({seeded / total * 100:.1f}%)\n"
            f"  Nodes with wikipedia.md: {with_article} ({with_article / total * 100:.1f}%)\n"
            f"  Cache directory: {cache_dir}\n"
        )
        return 0

    # Filter target nodes
    target_nodes = nodes
    if args.node:
        target_nodes = [n for n in nodes if n["id"] == args.node]
        if not target_nodes:
            print(f"Error: Node not found in taxonomy: {args.node}", file=sys.stderr)
            return 1
    elif args.limit:
        target_nodes = target_nodes[:args.limit]

    print(f"Seeding {len(target_nodes)} node(s) with Wikipedia content...")
    started = time.time()

    # Build children map
    children_map: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        pid = n["parent"]
        if pid:
            children_map.setdefault(pid, []).append(n)

    # Collect article titles to fetch
    titles = [n["title"] for n in target_nodes]
    print(f"Fetching / resolving {len(titles)} Wikipedia articles...")
    articles = fetch_wikipedia_batch(titles, cache_dir, force=args.force, offline=args.offline)

    seeded_count = 0
    for node in target_nodes:
        title = node["title"]
        data = articles.get(title, {})
        children = children_map.get(node["id"], [])

        seed_node(vault, node, data, children)
        seeded_count += 1
        sys.stdout.write(f"\r[{seeded_count}/{len(target_nodes)}] Seeded {node['id'][:55]:<55}")
        sys.stdout.flush()

    sys.stdout.write("\n")
    duration = time.time() - started
    print(f"✓ Successfully seeded {seeded_count} nodes in {duration:.2f}s!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
