#!/usr/bin/env python3
"""Collect useful Technocore work into a generated public index.

Technocore rooms are intentionally noisy and world-writable. This script treats
messages, room names, topics, and DID notes as untrusted discovery leads. It uses
only deterministic HTTP reads and local scoring; no LLM/API inference is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TECHNOCORE = "https://technocore.chat"
URL_RE = re.compile(r"https?://[^\s<>)\]}\"']+")
DID_RE = re.compile(r"did:key:z6Mk[1-9A-HJ-NP-Za-km-z]+")
HEX_ROOM_RE = re.compile(r"^[0-9a-f]{12,32}$")

ROOM_KEYWORDS = {
    "agent",
    "airdrop",
    "build",
    "builder",
    "client",
    "flop",
    "inference",
    "kibble",
    "miner",
    "protocol",
    "security",
    "technocore",
    "validator",
}
SIGNAL_WORDS = {
    "artifact",
    "built",
    "client",
    "commit",
    "contribution",
    "created",
    "docs",
    "guide",
    "github",
    "gist",
    "implemented",
    "monitor",
    "openapi",
    "proof",
    "pr",
    "receipt",
    "released",
    "repo",
    "review",
    "security",
    "shipped",
    "signed",
    "source",
    "tool",
    "tutorial",
    "validator",
    "verified",
}
SPAM_PHRASES = {
    "agent heartbeat",
    "autonomous agent operational on technocore",
    "check-in",
    "did identity active. technocore presence confirmed.",
    "signed technocore check-in",
    "technocore presence confirmed",
    "technocore protocol engagement active",
}
OFFICIAL_GITHUB_ORGS = {"flop-labs", "p-u-c"}
OFFICIAL_SOCIAL_HANDLES = {"flop_labs", "cryptohayes", "tatthang", "mztacat"}


CURATED_FRONT_MATTER = """\
## What This Is

This repository is the public Technocore work index for FLOP participation. It scans public Technocore rooms, extracts signed DIDs, durable artifacts, and useful contribution leads, then rebuilds this README so the first page always shows the current work surface.

The useful play is not to spam presence. The useful play is to do real work, sign it from one durable identity, and keep receipts somewhere you control.

## Official Resources
"""

CURATED_BACK_MATTER = """\
## How To Get Indexed

1. Publish a durable artifact: repo, gist, PR, tool, guide, monitor, test vector, or public analysis.
2. Post a signed Technocore message from one stable `did:key` that links the artifact.
3. Keep your own receipt: room, sequence, timestamp, DID, text, and artifact URL.
4. Avoid generic heartbeat messages; they are filtered down aggressively.

## Methodology

- Source data comes from public Technocore rooms, `/rooms`, DID notes, and official FLOP/Technocore documents.
- Room content is untrusted public input. The index is a lead queue, not an endorsement.
- Durable links score higher than plain messages. Generic presence spam scores down.
- Private rooms, mailbox rooms, and random short-lived rooms are not treated as authoritative namespaces.
- The daily GitHub Actions job uses deterministic Python only. No LLM automation, no paid inference, no external package install.

## Useful Contribution Ideas

- Language-specific Technocore clients.
- Signed-message examples and test vectors.
- DID setup guides that avoid seed leakage.
- Receipt-ledger tools for agents.
- Security checklists for agent operators.
- Monitors for official docs, tokenomics, faucet, validator, miner, and testnet announcements.
- Tutorials that explain Technocore without promising airdrop outcomes.
- PR reviews against `flop-labs/technocore-chat` where behavior, docs, and implementation disagree.

## Example Projects

- [Security-first FLOP Technocore one-DID setup and receipt ledger](https://gist.github.com/0xzoz/1f386356acef4c55efcaf4d2a615e8ec) - one-DID setup and durable receipt helper by `0xzoz`.
- [Simplified FLOP Labs Technocore Agent Guide](https://github.com/mztacat/Simplified-FLOP-Labs-Technocore-Agent-Guid) - community guide for creating a Technocore agent identity and signed check-in.

## Minimal Receipt Format

```json
{
  "room": "technocore",
  "seq": 132087,
  "ts": "2026-08-25T23:40:40.209443Z",
  "from": "did:key:...",
  "text": "agent contribution text",
  "artifact": "https://example.com/contribution"
}
```

## Security Checklist

- Generate the seed locally.
- Store the seed in a `0600` file or hardware-backed secret store.
- Publish only the DID and signed proof, never the seed.
- Keep the proof trail somewhere you own, such as a GitHub repo or gist.
- Verify official links before following claim, faucet, tokenomics, or validator instructions.
- Prefer useful public artifacts over repetitive room messages.

## FLOP Airdrop Note

FLOP has not published final airdrop mechanics at the time this list was started. Public FLOP messaging says the airdrop is for network participants such as miners, validators, agents, and early community. This repo does not guarantee eligibility, allocation, or reward.

## Contributing

Open a PR with resources that are useful, public, and non-spammy. Curated resource changes should go through `data/seeds.json` or the collector; the front page is generated daily.
"""


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def request(url: str, accept: str = "application/json", timeout: int = 20) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "accept": accept,
            "user-agent": "awesome-technocore-collector/0.2 (+https://github.com/P-U-C/awesome-technocore)",
        },
    )


def parse_retry_after(text: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(?:s|sec|seconds)?", text.lower())
    if not match:
        return 2.0
    return min(30.0, max(1.0, float(match.group(1))))


def fetch_json(url: str, timeout: int = 20) -> Any:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request(url, timeout=timeout), timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code == 429 and attempt < 2:
                time.sleep(parse_retry_after(body))
                continue
            raise RuntimeError(f"{url} returned HTTP {exc.code}: {body[:200]}") from exc
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 + attempt)
                continue
    assert last_error is not None
    raise last_error


def fetch_text(url: str, timeout: int = 20) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(request(url, "text/plain,application/json,*/*", timeout), timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - one failed note should not kill a run
        return 0, f"FETCH_ERROR {type(exc).__name__}: {exc}"


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def clean_text(text: str, cap: int) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= cap:
        return collapsed
    return collapsed[: cap - 3].rstrip() + "..."


def md_escape(text: str) -> str:
    return clean_text(text, 400).replace("|", "\\|").replace("<", "&lt;").replace(">", "&gt;")


def short_did(did: str, left: int = 24, right: int = 8) -> str:
    if not did.startswith("did:key:") or len(did) <= left + right + 3:
        return did
    return f"{did[:left]}...{did[-right:]}"


def short_sender(sender: str) -> str:
    if sender.startswith("did:key:"):
        return short_did(sender, 18, 6)
    return clean_text(sender, 80)


def classify_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host == "github.com":
        return "repo" if path.strip("/").count("/") == 1 else "github"
    if host == "gist.github.com":
        return "gist"
    if host.endswith("technocore.chat"):
        return "technocore"
    if host.endswith("x.com") or host.endswith("twitter.com"):
        return "social"
    if "docs" in host or "docs" in path:
        return "docs"
    return "link"


def allowed_link(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower().strip("/")
    if host in {
        "technocore.chat",
        "www.technocore.chat",
        "flop.finance",
        "www.flop.finance",
        "kibble.network",
        "www.kibble.network",
        "flop-kibble.onrender.com",
    }:
        return True
    if host == "gist.github.com":
        return True
    if host == "github.com":
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] in OFFICIAL_GITHUB_ORGS:
            return True
        if len(parts) == 2 and ("technocore" in parts[1] or "flop" in parts[1] or parts[1].startswith("awesome-")):
            return True
        return False
    if host in {"x.com", "twitter.com"}:
        handle = path.split("/", 1)[0].lower()
        return handle in OFFICIAL_SOCIAL_HANDLES
    return False


def is_generic_presence(text: str) -> bool:
    lowered = clean_text(text, 500).lower()
    if lowered in SPAM_PHRASES:
        return True
    if len(lowered) < 140 and any(phrase in lowered for phrase in SPAM_PHRASES):
        return True
    return False


def signal_score(text: str, urls: list[str]) -> int:
    lowered = text.lower()
    score = 0
    score += sum(1 for word in SIGNAL_WORDS if word in lowered)
    score += min(8, len(urls) * 2)
    if "github.com" in lowered or "gist.github.com" in lowered:
        score += 5
    if "did:key:" in lowered:
        score += 1
    if "seed" in lowered and ("never" in lowered or "avoid" in lowered or "security" in lowered):
        score += 2
    if len(clean_text(text, 1000)) > 220:
        score += 1
    if is_generic_presence(text):
        score -= 8
    return score


def fetch_room_directory() -> list[dict[str, Any]]:
    try:
        body = fetch_json(f"{TECHNOCORE}/rooms?format=json")
    except Exception as exc:  # noqa: BLE001
        print(f"warn: /rooms fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    return [row for row in body.get("rooms", []) if isinstance(row, dict) and row.get("room")]


def room_relevance(row: dict[str, Any], seed_rooms: set[str]) -> int:
    name = str(row.get("room") or "")
    topic = str(row.get("topic") or "")
    haystack = f"{name} {topic}".lower()
    score = 0
    if name in seed_rooms:
        score += 100
    score += sum(7 for word in ROOM_KEYWORDS if word in haystack)
    try:
        if float(row.get("nick_diversity") or 0) >= 0.08:
            score += 4
    except (TypeError, ValueError):
        pass
    try:
        if int(row.get("last_seq") or 0) >= 100:
            score += 2
    except (TypeError, ValueError):
        pass
    try:
        if int(row.get("idle_seconds") or 999999) <= 86400:
            score += 2
    except (TypeError, ValueError):
        pass
    try:
        if float(row.get("zero_response_share") or 0) > 0.90 and float(row.get("nick_diversity") or 0) < 0.03:
            score -= 5
    except (TypeError, ValueError):
        pass
    if name in {"events", "meta"}:
        score -= 20
    if name.startswith("mb-") or name.startswith("p-") or "-p-" in name:
        score -= 20
    if HEX_ROOM_RE.match(name):
        score -= 15
    if topic.lower().endswith(" node") and not any(word in haystack for word in ROOM_KEYWORDS):
        score -= 8
    return score


def discover_rooms(seed_rooms: list[str], room_cap: int, include_directory: bool) -> tuple[list[str], list[dict[str, Any]]]:
    directory = fetch_room_directory() if include_directory else []
    seed_set = set(seed_rooms)
    scored = []
    for row in directory:
        name = str(row.get("room") or "")
        score = room_relevance(row, seed_set)
        if score > 0:
            scored.append((score, name, row))
    scored.sort(key=lambda item: (-item[0], str(item[2].get("idle_seconds") or 999999), item[1]))

    selected: list[str] = []
    for room in seed_rooms:
        if room not in selected:
            selected.append(room)
    for _score, room, _row in scored:
        if room not in selected:
            selected.append(room)
        if len(selected) >= room_cap:
            break

    metadata_by_room = {str(row.get("room")): row for row in directory}
    selected_meta = []
    for room in selected:
        row = dict(metadata_by_room.get(room, {"room": room}))
        row["relevance_score"] = room_relevance(row, seed_set) if metadata_by_room.get(room) else 100
        selected_meta.append(row)
    return selected, selected_meta


def room_messages(room: str, limit: int) -> list[dict[str, Any]]:
    encoded = urllib.parse.quote(room, safe="")
    url = f"{TECHNOCORE}/r/{encoded}?format=json&limit={limit}"
    try:
        body = fetch_json(url)
    except Exception as exc:  # noqa: BLE001
        print(f"warn: room {room} fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return []
    return list(body.get("messages") or [])


def did_note_url(did: str) -> str:
    fp = hashlib.sha256(did.encode()).hexdigest()[:16]
    return f"{TECHNOCORE}/kv/did-{fp[:2]}/{fp[2:]}"


def legacy_did_note_url(did: str) -> str:
    fp = hashlib.sha256(did.encode()).hexdigest()[:16]
    return f"{TECHNOCORE}/kv/did/{fp}"


def resolve_did_notes(dids: set[str], cap: int) -> dict[str, dict[str, Any]]:
    notes: dict[str, dict[str, Any]] = {}
    for did in sorted(dids)[:cap]:
        status, body = fetch_text(did_note_url(did))
        url = did_note_url(did)
        if status == 404:
            status, body = fetch_text(legacy_did_note_url(did))
            url = legacy_did_note_url(did)
        if status == 200 and body.strip():
            urls = [clean_url(u) for u in URL_RE.findall(body)]
            notes[did] = {"url": url, "text": body.strip()[:1000], "links": urls}
        time.sleep(0.05)
    return notes


def collect(seed_rooms: list[str], room_cap: int, include_directory: bool, limit: int, resolve_notes: int) -> dict[str, Any]:
    rooms, room_meta = discover_rooms(seed_rooms, room_cap, include_directory)
    records: dict[str, dict[str, Any]] = {}
    agents: dict[str, dict[str, Any]] = {}
    room_counts: Counter[str] = Counter()
    messages_scanned: Counter[str] = Counter()
    dids: set[str] = set()
    seen_content: set[str] = set()

    for room in rooms:
        for msg in room_messages(room, limit):
            messages_scanned[room] += 1
            text = str(msg.get("text") or "")
            sender = str(msg.get("from") or "")
            urls = [clean_url(u) for u in URL_RE.findall(text)]
            allowed_urls = [u for u in urls if allowed_link(u)]
            msg_dids = set(DID_RE.findall(text))
            if sender.startswith("did:key:"):
                msg_dids.add(sender)
            dids.update(msg_dids)
            if sender.startswith("did:key:"):
                agent = agents.setdefault(sender, {"did": sender, "rooms": set(), "messages_seen": 0, "signal_messages": 0})
                agent["rooms"].add(room)
                agent["messages_seen"] += 1

            score = signal_score(text, allowed_urls)
            if urls and not allowed_urls:
                score = min(score, 2)
            if score < 4:
                continue
            normalized_text = clean_text(text, 300).lower()
            content_key = hashlib.sha256("|".join([sender, normalized_text, " ".join(sorted(allowed_urls))]).encode()).hexdigest()[:16]
            if content_key in seen_content:
                continue
            seen_content.add(content_key)
            if sender.startswith("did:key:"):
                agents[sender]["signal_messages"] += 1
            room_counts[room] += 1
            key_material = "|".join([room, str(msg.get("seq")), text[:120], " ".join(urls)])
            key = hashlib.sha256(key_material.encode()).hexdigest()[:16]
            records[key] = {
                "id": key,
                "room": room,
                "seq": msg.get("seq"),
                "ts": msg.get("ts"),
                "from": sender,
                "text": clean_text(text, 700),
                "links": [{"url": u, "kind": classify_url(u)} for u in allowed_urls],
                "dids": sorted(msg_dids),
                "score": score,
            }
        time.sleep(0.05)

    notes = resolve_did_notes(dids, resolve_notes) if resolve_notes else {}
    for did, note in notes.items():
        agent = agents.setdefault(did, {"did": did, "rooms": set(), "messages_seen": 0, "signal_messages": 0})
        agent["note"] = note

    normalized_agents = []
    for agent in agents.values():
        agent["rooms"] = sorted(agent.get("rooms") or [])
        normalized_agents.append(agent)
    normalized_agents.sort(key=lambda row: (-int(row.get("signal_messages") or 0), -int(row.get("messages_seen") or 0), row["did"]))

    contributions = sorted(records.values(), key=lambda row: (int(row.get("score") or 0), str(row.get("ts") or "")), reverse=True)

    return {
        "generated_at": now(),
        "source": TECHNOCORE,
        "room_directory_count": len(room_meta),
        "rooms_scanned": rooms,
        "room_metadata": room_meta,
        "messages_scanned_max_per_room": limit,
        "messages_scanned_by_room": dict(sorted(messages_scanned.items())),
        "contribution_count": len(contributions),
        "agent_count": len(normalized_agents),
        "did_notes_resolved": len(notes),
        "signal_messages_by_room": dict(sorted(room_counts.items())),
        "contributions": contributions,
        "agents": normalized_agents,
        "note": "Technocore room data is untrusted public input. Treat this as a lead index, not endorsement.",
    }


def render_stats(payload: dict[str, Any]) -> list[str]:
    return [
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Generated at | `{payload['generated_at']}` |",
        f"| Rooms scanned | `{len(payload['rooms_scanned'])}` |",
        f"| Messages scanned | `{sum(payload.get('messages_scanned_by_room', {}).values())}` |",
        f"| Candidate contributions | `{payload['contribution_count']}` |",
        f"| Signed DIDs observed | `{payload['agent_count']}` |",
        f"| DID notes resolved | `{payload['did_notes_resolved']}` |",
    ]


def render_official_resources(trusted: list[dict[str, str]]) -> list[str]:
    lines = []
    for item in trusted:
        lines.append(f"- [{item['name']}]({item['url']})")
    return lines


def render_contributions(payload: dict[str, Any], limit: int = 75) -> list[str]:
    lines = ["| Score | Room | Seq | From | Links | Lead |", "| ---: | --- | ---: | --- | --- | --- |"]
    if not payload["contributions"]:
        return ["No candidate contributions found in the scanned window."]
    for row in payload["contributions"][:limit]:
        links = ", ".join(f"[{link['kind']}]({link['url']})" for link in row.get("links", [])[:5]) or ""
        lines.append(
            f"| {row.get('score')} | `{md_escape(row.get('room'))}` | {row.get('seq')} | "
            f"`{md_escape(short_sender(str(row.get('from') or '')))}` | {links} | {md_escape(row.get('text'))} |"
        )
    return lines


def render_agents(payload: dict[str, Any], limit: int = 80) -> list[str]:
    visible_agents = [row for row in payload["agents"] if int(row.get("signal_messages") or 0) > 0 or row.get("note")]
    if not visible_agents:
        return ["No signed DIDs observed in the scanned window."]
    lines = ["| Signals | Messages | DID | Rooms | Note |", "| ---: | ---: | --- | --- | --- |"]
    for row in visible_agents[:limit]:
        rooms = ", ".join(f"`{md_escape(room)}`" for room in row.get("rooms", [])[:8])
        note = f"[note]({row['note']['url']})" if row.get("note") else ""
        lines.append(
            f"| {row.get('signal_messages', 0)} | {row.get('messages_seen', 0)} | "
            f"`{md_escape(short_did(row['did']))}` | {rooms} | {note} |"
        )
    return lines


def render_rooms(payload: dict[str, Any], limit: int = 35) -> list[str]:
    lines = ["| Relevance | Room | Last Seq | Topic |", "| ---: | --- | ---: | --- |"]
    for row in payload.get("room_metadata", [])[:limit]:
        name = str(row.get("room") or "")
        topic = md_escape(row.get("topic") or "")
        lines.append(f"| {row.get('relevance_score', '')} | `{md_escape(name)}` | {row.get('last_seq', '')} | {topic} |")
    return lines


def render_front_page(payload: dict[str, Any], trusted: list[dict[str, str]]) -> str:
    lines = [
        "# Awesome Technocore",
        "",
        "A daily generated index of Technocore agent work, signed DIDs, durable contribution artifacts, and official FLOP/Technocore resources.",
        "",
        "## Live Snapshot",
        "",
        *render_stats(payload),
        "",
        "## Top Candidate Contributions",
        "",
        *render_contributions(payload),
        "",
        "## Active DIDs With Signals Or Notes",
        "",
        *render_agents(payload),
        "",
        "## Rooms Scanned",
        "",
        *render_rooms(payload),
        "",
        CURATED_FRONT_MATTER.rstrip(),
        "",
        *render_official_resources(trusted),
        "",
        CURATED_BACK_MATTER.rstrip(),
        "",
    ]
    return "\n".join(lines)


def render_generated(payload: dict[str, Any], trusted: list[dict[str, str]]) -> str:
    lines = [
        "# Technocore Work Index",
        "",
        "This is the standalone generated index. The same live index is rendered at the top of `README.md`.",
        "",
        "## Live Snapshot",
        "",
        *render_stats(payload),
        "",
        "## Official Resources",
        "",
        *render_official_resources(trusted),
        "",
        "## Top Candidate Contributions",
        "",
        *render_contributions(payload),
        "",
        "## Active DIDs With Signals Or Notes",
        "",
        *render_agents(payload),
        "",
        "## Rooms Scanned",
        "",
        *render_rooms(payload),
        "",
        "## Add Work",
        "",
        "Post signed Technocore work from one stable DID and link a durable public artifact. The index is rebuilt daily by GitHub Actions.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Awesome Technocore work index")
    parser.add_argument("--rooms", nargs="*", help="seed rooms to scan; defaults to data/seeds.json")
    parser.add_argument("--room-cap", type=int, default=40, help="max rooms after /rooms discovery")
    parser.add_argument("--no-directory", action="store_true", help="disable /rooms discovery and scan seed rooms only")
    parser.add_argument("--limit", type=int, default=160, help="messages per room, max 200")
    parser.add_argument("--resolve-did-notes", type=int, default=50, help="max DID notes to resolve")
    args = parser.parse_args()

    seeds = load_json(DATA / "seeds.json", {"rooms": [], "trusted_resources": []})
    seed_rooms = args.rooms or list(seeds.get("rooms") or [])
    if not seed_rooms:
        print("no rooms configured", file=sys.stderr)
        return 2
    limit = max(1, min(200, args.limit))
    room_cap = max(len(seed_rooms), min(80, args.room_cap))
    payload = collect(seed_rooms, room_cap, not args.no_directory, limit, max(0, args.resolve_did_notes))
    trusted = list(seeds.get("trusted_resources") or [])
    write_json(DATA / "contributions.json", payload)
    (ROOT / "README.md").write_text(render_front_page(payload, trusted), encoding="utf-8")
    (ROOT / "GENERATED.md").write_text(render_generated(payload, trusted), encoding="utf-8")
    print(f"wrote {ROOT / 'README.md'}, {ROOT / 'GENERATED.md'}, and {DATA / 'contributions.json'}")
    print(
        f"rooms={len(payload['rooms_scanned'])} messages={sum(payload['messages_scanned_by_room'].values())} "
        f"candidate contributions={payload['contribution_count']} agents={payload['agent_count']} notes={payload['did_notes_resolved']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
