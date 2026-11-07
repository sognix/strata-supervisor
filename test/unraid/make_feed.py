"""Build a local Community Applications feed with the Strata Supervisor template in it.

CA builds its tag selection ("branches") only for apps from its feed, not for private templates. Its
developer switch `localONLY` makes it read the feed from a local file instead, so this script puts our
template into a copy of the real feed. Usage (see UNRAID-TEST.md):

    python3 -I test/unraid/make_feed.py test/unraid/applicationFeed.orig.json test/unraid/applicationFeed.json

The tags are rewritten for testing: `latest` becomes `dev` and `latest-<flavor>` becomes `dev-<flavor>`,
because CI publishes those on every merge into main (pass --tag latest to keep the release tags).
"""

from __future__ import annotations

import argparse
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "unraid" / "strata-supervisor.xml"
REPO_NAME = "sognix's Repository"
REPO_URL = "https://github.com/sognix/strata-supervisor"
# Fields that the feed keeps as lists of objects; everything else is plain text.
SKIP = {"Config", "Branch", "Category"}


def config_entry(el: ET.Element) -> dict:
    """A <Config> element in the feed's shape: attributes under "@attributes", the text as "value"."""
    return {"@attributes": dict(el.attrib), "value": el.text or ""}


def retag(tag: str, prefix: str) -> str:
    """latest -> dev, latest-cuda12 -> dev-cuda12 (prefix "dev")."""
    if tag == "latest":
        return prefix
    if tag.startswith("latest-"):
        return prefix + tag[len("latest"):]
    return tag


def template_entry(prefix: str) -> dict:
    root = ET.parse(TEMPLATE).getroot()
    entry: dict = {}
    for child in root:
        if child.tag not in SKIP and len(child) == 0:
            entry[child.tag] = child.text or ""
    entry["Config"] = [config_entry(c) for c in root.findall("Config")]

    image, _, tag = entry["Repository"].rpartition(":")
    entry["Repository"] = f"{image}:{retag(tag, prefix)}"

    branches = []
    for b in root.findall("Branch"):
        branch: dict = {}
        for child in b:
            if child.tag == "Config":
                branch.setdefault("Config", []).append(config_entry(child))
            else:
                branch[child.tag] = child.text or ""
        branch["Tag"] = retag(branch["Tag"], prefix)
        branches.append(branch)
    entry["Branch"] = branches

    # "AI: Tools:Utilities" -> ["AI", "Tools-Utilities"], as the feed lists categories.
    category = root.findtext("Category") or ""
    entry["CategoryList"] = [c.rstrip(":").replace(":", "-") for c in category.split()]
    entry["Repo"] = REPO_NAME
    entry["FirstSeen"] = int(time.time())
    entry["ExtraSearchTerms"] = "strata llm gpu"
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("feed_in", type=Path, help="the real feed, downloaded from assets.ca.unraid.net")
    parser.add_argument("feed_out", type=Path, help="the feed for the server, with our template")
    parser.add_argument("--tag", default="dev", help="tag prefix instead of 'latest' (default: dev)")
    args = parser.parse_args()

    feed = json.loads(args.feed_in.read_text(encoding="utf-8"))
    entry = template_entry(args.tag)
    feed["applist"] = [a for a in feed["applist"] if a.get("Repo") != REPO_NAME] + [entry]
    feed.setdefault("repositories", {})[REPO_NAME] = {"url": REPO_URL}
    # A new timestamp, so CA does not keep a cached copy of the old feed.
    feed["last_updated_timestamp"] = int(time.time())
    args.feed_out.write_text(json.dumps(feed, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    print(f"Wrote {args.feed_out} with {len(feed['applist'])} apps.")
    print(f"Default: {entry['Repository']}")
    for b in entry["Branch"]:
        print(f"Branch:  {b['Tag']:<22} {b.get('ExtraParams', '(template default ExtraParams)')}")


if __name__ == "__main__":
    main()
