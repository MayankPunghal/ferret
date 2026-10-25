# Plans not yet built (catalogue, 2026-10-24)

Every written plan that is still open, in the order I suggest building them. [TODO.md](TODO.md) is the full
list of open work (bugs, measurements, owner checks, ideas without a plan); this page covers only the items
that have a plan file. Sizes marked *(est.)* are my estimate; the others are stated in the plan.

| Order | Plan | Goal in one line | Status | Size | Waits for | TODO # |
|---|---|---|---|---|---|---|
| 4 | [plan-gpu.md](plan-gpu.md) | CPU/GPU switch in Settings for search models and the local language model; kept only where measured faster with identical results | **summaries switch built 2026-10-25**; embeddings and reranker stay on the CPU (owner decision: CPU faster on this laptop) | — | a dedicated high-memory GPU, someday | 4 |
| 5 | [plan-release.md](plan-release.md) | CI on Linux, Windows, macOS → PyPI package with a browser setup wizard → Docker image on GHCR → Windows installer → macOS | steps 1 (CI), 2 (PyPI packages, no wizard) and 3 (Docker) **built 2026-10-25**, first publish waits for the owner's PyPI setup; steps 4–5 held (owner) | — | — | 5 |
| 6 | [plan-docker.md](plan-docker.md) | Docker image and compose file brought up to date (search model, summaries model, writable model folder, self-test) | gaps 1–3, 6, 7 **built 2026-10-25**; memory figures and the CI backup check not done | — | — | 6 |
| 7 | [plan-installer.md](plan-installer.md) | One-click Windows installer like *holler* (PyInstaller + Inno Setup, tray icon, updates, uninstall keeps data) | **held** (owner, later phase) | medium–large *(est.)* | — | 5 |
| 8 | [plan-invites.md](plan-invites.md) | Invite and password-reset links without an email server; change password; slow down password guessing | **held** (owner, later phase) | small–medium *(est.)* | — | 7 |
| 9 | [plan-answers.md](plan-answers.md) | **Ask**: a chat window where a local model answers from search results with checked citations; saved conversations, pictures, Think first | **held** (owner, later phase) | large, in 6 phases; phase 1 medium | — | 16 |
| 10 | [plan-images-and-models.md](plan-images-and-models.md) | Images saved by the crawler and in folders, their text written by a local vision model and indexed | on hold (owner); step 5, summaries, **built** | medium–large *(est.)* | the Gemma E2B vision run (TODO 2) and the owner's go-ahead | 10 |

**Built, kept for reference:** [plan-large-documents.md](plan-large-documents.md) (steps 2–3 built 2026-10-25; step 1 only with a measured gain, TODO 3), [plan-tables.md](plan-tables.md) (steps 1–4 built 2026-10-25; step 5, a measurement, is TODO 26), [plan-summaries-status.md](plan-summaries-status.md) (Summaries counts and server paging of the documents list, built 2026-10-25), [plan-site2md-ui.md](plan-site2md-ui.md) (Ferret Crawler screen, built
2026-10-24) and step 5 of plan-images-and-models (document summaries, built 2026-10-23).

## Why this order

- **1** first because it fixes a bug users hit past 1,000 documents, and it is small.
- **2** next: wrong tables hurt both reading and search on every crawled site with tables, and the fix is
  contained (one converter shared by both apps, plus passage splitting).
- **3–4** make indexing faster without changing results; GPU needs the owner's hardware to measure.
- **5–7** are the release path; CI comes first because packages are not trustworthy without it.
- **8** is needed before inviting a team, but nothing else depends on it.
- **9** is the largest piece; it is optional for people with Claude and MCP, so it comes after the
  install and release work that every user needs. Phase 1 (answers without pictures) can start earlier if
  the owner prefers.
- **10** stays on hold until the vision measurement and the owner decide.

## Ideas with no plan yet

TODO §5 (items 17–23): entity links and PageRank-style spreading, SPLADE, graph clusters, OCR/Docling for
PDFs (including PDF tables), the archive-size scale test, a real Graphify wiki evaluation, a larger summary
model. Each gets a plan only when someone is about to measure it.

Published names (owner, 2026-10-25): PyPI package **`ferret-search`**, image **`ghcr.io/mayankpunghal/ferret-search`**; the plans use them.