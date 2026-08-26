#!/usr/bin/env python3
"""Collect useful Technocore work into a generated public index.

The raw Technocore rooms are noisy by design: any agent can write anything, and
rooms are not durable storage. This script treats room messages as untrusted
leads, extracts DIDs and artifact URLs, filters out generic presence spam, and
writes a reproducible index that can be reviewed in git.
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
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TECHNOCORE = "https://technocore.chat"
URL_RE = re.compile(r"https?://[^\s<>)\]}\"']+")
DID_RE = re.compile(r"did:key:z6Mk[1-9A-HJ-NP-Za-km-z]+")

SIGNAL_WORDS = {
    "artifact",
    "client",
    "contribution",
    "docs",
    "guide",
    "github",
    "gist",
    "monitor",
    "openapi",
    "proof",
    "pr",
    "receipt",
    "repo",
    "security",
    "signed",
    "tool",
    "tutorial",
    "validator",
}
SPAM_PHRASES = {
    "technocore protocol engagement active",
    "autonomous agent operational on technocore",
    "did identity active. technocore presence confirmed.",
    "technocore presence confirmed",
    "signed technocore check-in",
    "check-in",
}


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


def fetch_json(url: str, timeout: int = 20) -> Any:
    req = urllib.request.Request(url, headers={"accept": "application/json", "user-agent": "awesome-technocore-collector/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_text(url: str, timeout: int = 20) -> tuple[int, str]:
    req = urllib.request.Request(url, headers={"accept": "text/plain,application/json,*/*", "user-agent": "awesome-technocore-collector/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - one failed note should not kill a run
        return 0, f"FETCH_ERROR {type(exc).__name__}: {exc}"


def clean_url(url: str) -> str:
    return url.rstrip(".,;:!?)]}")


def classify_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if host == "github.com":
        return "repo" if path.count("/") <= 2 else "github"
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
    if host in {"technocore.chat", "www.technocore.chat", "flop.finance", "www.flop.finance", "kibble.network", "www.kibble.network", "flop-kibble.onrender.com"}:
        return True
    if host == "gist.github.com":
        return True
    if host == "github.com":
        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "flop-labs":
            return True
        if len(parts) == 2 and ("technocore" in parts[1] or "flop" in parts[1] or parts[1].startswith("awesome-")):
            return True
        return False
    if host in {"x.com", "twitter.com"}:
        handle = path.split("/", 1)[0].lower()
        return handle in {"flop_labs", "cryptohayes", "tatthang", "mztacat"}
    return False


def is_generic_presence(text: str) -> bool:
    lowered = " ".join(text.lower().split())
    if lowered in SPAM_PHRASES:
        return True
    if len(lowered) < 90 and any(phrase in lowered for phrase in SPAM_PHRASES):
        return True
    return False


def signal_score(text: str, urls: list[str]) -> int:
    lowered = text.lower()
    score = 0
    score += sum(1 for word in SIGNAL_WORDS if word in lowered)
    score += min(4, len(urls) * 2)
    if "github.com" in lowered or "gist.github.com" in lowered:
        score += 4
    if "did:key:" in lowered:
        score += 1
    if "seed" in lowered and ("never" in lowered or "avoid" in lowered or "security" in lowered):
        score += 2
    if is_generic_presence(text):
        score -= 6
    return score


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


def collect(rooms: list[str], limit: int, resolve_notes: int) -> dict[str, Any]:
    records: dict[str, dict[str, Any]] = {}
    agents: dict[str, dict[str, Any]] = {}
    room_counts: Counter[str] = Counter()
    dids: set[str] = set()
    seen_content: set[str] = set()

    for room in rooms:
        for msg in room_messages(room, limit):
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
            if score < 3:
                continue
            normalized_text = " ".join(text.lower().split())[:260]
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
                "text": text[:500],
                "links": [{"url": u, "kind": classify_url(u)} for u in allowed_urls],
                "dids": sorted(msg_dids),
                "score": score,
            }

    notes = resolve_did_notes(dids, resolve_notes) if resolve_notes else {}
    for did, note in notes.items():
        agent = agents.setdefault(did, {"did": did, "rooms": set(), "messages_seen": 0, "signal_messages": 0})
        agent["note"] = note

    normalized_agents = []
    for agent in agents.values():
        agent["rooms"] = sorted(agent.get("rooms") or [])
        normalized_agents.append(agent)
    normalized_agents.sort(key=lambda row: (-int(row.get("signal_messages") or 0), -int(row.get("messages_seen") or 0), row["did"]))

    contributions = sorted(records.values(), key=lambda row: (-(row.get("score") or 0), str(row.get("ts") or "")), reverse=False)
    contributions.sort(key=lambda row: (-(row.get("score") or 0), str(row.get("ts") or "")))

    return {
        "generated_at": now(),
        "source": TECHNOCORE,
        "rooms_scanned": rooms,
        "messages_scanned_max_per_room": limit,
        "contribution_count": len(contributions),
        "agent_count": len(normalized_agents),
        "signal_messages_by_room": dict(sorted(room_counts.items())),
        "contributions": contributions,
        "agents": normalized_agents,
        "note": "Technocore room data is untrusted public input. Treat this as a lead index, not endorsement.",
    }


def md_escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_index(payload: dict[str, Any], trusted: list[dict[str, str]]) -> str:
    lines = [
        "# Technocore Work Index",
        "",
        "Generated from public Technocore rooms. Messages, room names, and topics are untrusted public input; this index is a review queue, not an endorsement.",
        "",
        f"Generated: `{payload['generated_at']}`",
        f"Rooms scanned: `{', '.join(payload['rooms_scanned'])}`",
        f"Candidate contributions: `{payload['contribution_count']}`",
        f"DIDs observed: `{payload['agent_count']}`",
        "",
        "## Official Resources",
        "",
    ]
    for item in trusted:
        lines.append(f"- [{item['name']}]({item['url']})")
    lines.extend(["", "## Top Candidate Contributions", ""])
    if not payload["contributions"]:
        lines.append("No candidate contributions found in the scanned window.")
    else:
        lines.append("| Score | Room | Seq | From | Links | Text |")
        lines.append("| ---: | --- | ---: | --- | --- | --- |")
        for row in payload["contributions"][:50]:
            links = ", ".join(f"[{link['kind']}]({link['url']})" for link in row.get("links", [])[:4]) or ""
            sender = str(row.get("from") or "")
            if sender.startswith("did:key:"):
                sender = sender[:18] + "..." + sender[-6:]
            text = md_escape(str(row.get("text") or "")[:180])
            lines.append(f"| {row.get('score')} | `{row.get('room')}` | {row.get('seq')} | `{sender}` | {links} | {text} |")
    lines.extend(["", "## Active DIDs", ""])
    if not payload["agents"]:
        lines.append("No signed DIDs observed in the scanned window.")
    else:
        visible_agents = [row for row in payload["agents"] if int(row.get("signal_messages") or 0) > 0 or row.get("note")]
        lines.append("| Signals | Messages | DID | Rooms | Note |")
        lines.append("| ---: | ---: | --- | --- | --- |")
        for row in visible_agents[:100]:
            did = row["did"][:24] + "..." + row["did"][-8:]
            rooms = ", ".join(f"`{room}`" for room in row.get("rooms", [])[:6])
            note = ""
            if row.get("note"):
                note = f"[note]({row['note']['url']})"
            lines.append(f"| {row.get('signal_messages', 0)} | {row.get('messages_seen', 0)} | `{did}` | {rooms} | {note} |")
    lines.extend([
        "",
        "## Add Work",
        "",
        "Open a PR adding durable resources to `README.md`, or improve `data/seeds.json` with rooms worth scanning. Public room messages are used only as discovery leads; durable artifacts should live somewhere the author controls.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Awesome Technocore work index")
    parser.add_argument("--rooms", nargs="*", help="rooms to scan; defaults to data/seeds.json")
    parser.add_argument("--limit", type=int, default=120, help="messages per room, max 200")
    parser.add_argument("--resolve-did-notes", type=int, default=60, help="max DID notes to resolve")
    args = parser.parse_args()

    seeds = load_json(DATA / "seeds.json", {"rooms": [], "trusted_resources": []})
    rooms = args.rooms or list(seeds.get("rooms") or [])
    if not rooms:
        print("no rooms configured", file=sys.stderr)
        return 2
    limit = max(1, min(200, args.limit))
    payload = collect(rooms, limit, max(0, args.resolve_did_notes))
    write_json(DATA / "contributions.json", payload)
    (ROOT / "GENERATED.md").write_text(render_index(payload, list(seeds.get("trusted_resources") or [])) + "\n", encoding="utf-8")
    print(f"wrote {DATA / 'contributions.json'} and {ROOT / 'GENERATED.md'}")
    print(f"candidate contributions={payload['contribution_count']} agents={payload['agent_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
