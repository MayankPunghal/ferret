# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `setup.bat` window closes or says *uv could not be installed* | No internet or a proxy blocks `astral.sh`. Set `HTTPS_PROXY` and retry, or install uv manually from <https://docs.astral.sh/uv/>. |
| Setup fails at *Downloading the offline AI models* | Hugging Face is blocked. Copy `data/models` from a machine where setup worked, then set `KS_OFFLINE=1`. |
| Browser does not open | Open <http://127.0.0.1:8000> manually. |
| *Port 8000 already in use* | Another app uses it: `uv run knowledge-search serve --port 8010 --open`. |
| Welcome page says setup is only allowed from this machine | First-run admin creation is restricted to the computer running the app. Open the page there. |
| Document stuck in **pending** | No indexing worker is running (server profile: start `knowledge-search worker`). Large models index slowly on CPU — check Index status. |
| Document **failed** — `no_extractable_text` | Scanned PDF without a text layer; OCR is not supported. |
| Document **failed** — `invalid_encoding` | Text file is not UTF-8; re-save it as UTF-8. |
| Search says **indexing incomplete** | Some documents in your scope are pending, failed or outdated (settings changed). Admin: Settings → Reindex. |
| Search says **no adequate evidence** but a lower-relevance match looks right | The evidence check is conservative by design. Admins can enable the reranker or adjust thresholds (Settings). |
| Search says **retrieval degraded** | A model could not be loaded (e.g. just switched and still downloading). Check Settings → model status. |
| Embedded database will not start after a crash | Stop all app windows, then delete `data/pg/postmaster.pid` if it exists and start again. |
| Running as root on Linux | The embedded PostgreSQL refuses root; it creates and uses a `pgserver` system user automatically. |
