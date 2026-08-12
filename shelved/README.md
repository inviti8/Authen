# Shelved — the Pintheon comics build

Kept, not deleted. Authen pivoted from selling comic issues over x402 to selling
notarization; this directory is what the comics product was, parked intact in case
it is revived.

| Path | Was |
|---|---|
| `comics_content/library.py` | Title/issue catalogue, page and preview resolution, the paywall boundary (`preview_pages`) |
| `make_placeholder_content.py` | Synthetic page generator used before real books were staged |

**Why it was shelved** — the live x402 field prices resources between $0.005 and
$0.50, with not one resource above $1 across 1,204 listings. A $3.00 comic issue is
six times the most expensive thing on the rail. It is a machine-to-machine API
micropayment market, and no agent shops for comics; meanwhile a creator will not
hand a crypto-native paywall URL to their audience. The consumer path for comics is
the portal card rail (`heavymeta_collective`), not x402.

Nothing here is imported by `authen/`. It is dead weight in the tree on purpose —
delete it only if the comics idea is genuinely abandoned.
