# Contributing

Useful submissions are welcome. The README is generated daily, so do not hand-edit the live index directly.

## Good Additions

- Official Technocore or FLOP resources in `data/seeds.json`.
- Rooms worth scanning because they produce durable work, not just presence.
- Working clients, examples, and test vectors.
- Security-first DID and receipt tooling.
- Clear tutorials that do not promise an airdrop.
- Monitors or workflows that help agents contribute without spam.

## Not Accepted

- Fake claim links or token contracts.
- Referral-only pages.
- Empty heartbeat scripts.
- Multi-DID farming guides.
- Seed-handling instructions that ask users to paste secrets into chat or web forms.
- Manual edits to generated README tables.

## Format

For curated resources, edit `data/seeds.json` and include:

- `name`
- `url`

For scanned rooms, add the room name to `rooms` only when the room has recurring useful work or a clear FLOP/Technocore purpose.

Then run:

```bash
python3 scripts/collect.py
```

Keep the list curated. A smaller useful list is better than a large noisy one.
