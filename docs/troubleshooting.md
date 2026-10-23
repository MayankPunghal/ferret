# Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `setup.bat` window closes or says *uv could not be installed* | No internet or a proxy blocks `astral.sh`. Set `HTTPS_PROXY` and retry, or install uv manually from <https://docs.astral.sh/uv/>. |
| Setup fails at *Downloading the offline AI models* (e.g. `WinError 10054` / `10038`) | The connection dropped. Downloads are retried 5× with backoff and finished files are kept — run setup again. Behind a proxy/firewall allow `huggingface.co` and `*.hf.co`, or copy `data/models` from a machine where setup worked and set `KS_OFFLINE=1`. |
| Browser does not open | Open <http://127.0.0.1:8000> manually. |
| *Port 8000 already in use* | Another app uses it: `uv run knowledge-search serve --port 8010 --open`. |
| Welcome page says setup is only allowed from this machine | First-run admin creation is restricted to the computer running the app. Open the page there. |
| Document stuck in **pending** | No indexing worker is running (server profile: start `knowledge-search worker`). Large models index slowly on CPU — check Index status. |
| Document **failed** — `no_extractable_text` | Scanned PDF without a text layer; OCR is not supported. |
| Document **failed** — `invalid_encoding` | Text file is not UTF-8; re-save it as UTF-8. |
| Search says **indexing incomplete** | Some documents in your scope are pending, failed or outdated (settings changed). Admin: Settings → Reindex. |
| Search says **no adequate evidence** but one of the closest matches looks right | The evidence check is conservative by design (it is tuned to avoid false answers). Admins can try the stronger `bge-reranker-base` or adjust thresholds in Settings; see docs/evaluation.md for the trade-off. |
| Search says **retrieval degraded** | A model could not be loaded (e.g. just switched and still downloading). Check Settings → model status. |
| Embedded database will not start after a crash | Stop all app windows, then delete `data/pg/postmaster.pid` if it exists and start again. |
| Console shows `ConnectionResetError: [WinError 10054]` in `_call_connection_lost` (Windows) | Harmless: the browser closed an idle connection. Filtered since v0.2; nothing to fix. |
| Index status says **no local language model installed** or **the summary model could not be downloaded** | Summaries are on, but the model is not there and could not be downloaded (offline, `KS_OFFLINE=true`, or a proxy). The app retries at every start; allow `github.com` and `huggingface.co`, or run `uv run knowledge-search download-llm` once. Search works without it. |
| Index status says **the local language model could not start** | See `data/logs/llm-server.log`. Usual causes: antivirus blocked `llama-server.exe` (allow it), or too little free RAM (~4 GB for Gemma 4 E2B, ~1 GB for Qwen3.5-0.8B). Fix, then restart the app; summaries resume where they stopped. |
| Summaries stay at "waiting for indexing" | By design: summaries are written only while no files wait to be indexed. They start when the Documents tab shows nothing pending. |
| Anything else / you need help | Admin → **Logs** → **Copy for support** and paste it into your report (contains versions, settings and errors with tracebacks; never document or search text). The same log is in `data/logs/knowledge-search.log`. |
| Running as root on Linux | The embedded PostgreSQL refuses root; it creates and uses a `pgserver` system user automatically. |
