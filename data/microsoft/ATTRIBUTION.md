# Attribution — vendored Microsoft documentation

The twelve `.md` files in this folder are unmodified copies of pages from
**Microsoft Windows Server documentation**:

- Source: <https://github.com/MicrosoftDocs/windowsserverdocs>
- Commit: `824bd981ac0e149fc228667d7d781ade36079935` (pinned — ADR 0012)
- Licence: Creative Commons Attribution 4.0 International (CC BY 4.0) —
  <https://creativecommons.org/licenses/by/4.0/>
- © Microsoft Corporation

Each page's upstream path is recorded on its row in `data/corpus.jsonl`
(`upstream_path`). No page has been edited; file names are the upstream
basenames. They were fetched once by `tools/fetch_ms_slice.py`, which checks each
file's byte count against the size measured when the list was chosen.

This file is deliberately **not** listed in the manifest, so it is never indexed:
corpus membership is the manifest's list, not whatever sits in a folder (ADR 0011).
