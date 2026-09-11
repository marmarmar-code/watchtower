# Captured public source fixtures

These files were read from official public sources on 2026-09-11. They are
fixed parser-regression inputs, not current data or proof of new events.
EMA and Nye metoder workbooks retain the full captured tables so duplicate,
category and indication counts can be checked offline on every platform.
The Nye metoder page supplies the workbook-discovery link; its line endings
and trailing whitespace are normalized for Git. Original response bytes remain
in the separately documented live-read evidence. The industrial
JSON is a small selected Yara response; tests explicitly alter/add selected
attributes to exercise validation. Those mutations are synthetic tests.

Source contracts and live-read evidence are documented in the corresponding
`source_checks/2026-09-11-*.md` and `final-verification.json` files. No private
runtime state or credentials are included. Tests do not fetch these files
from the network and do not depend on the developer's temporary directory.

Captured file SHA-256 values:

- `ema-medicines-2026-09-11.xlsx`: `5e95f4815626dae083e0621efbfcd23d8095ed13f528dd556c4a5edbb7831e94`
- `nye-metoder-page-2026-09-11.html`: `4c8ada439959d533822f83ec771c63d8c48ba8ac625618ae1a7d0dc8a2661265`
- `nye-metoder-decisions-2026-09-11.xlsx`: `aa88fff98230764936014ada1f5335b3b2ea1ebb9c9f970139817cca41a7b672`
- `industrial-yara-2026-09-11.json`: `230e1a14b61a3e325995550e434d542233c2053264f0f4b1892ae6bc38d26877`
