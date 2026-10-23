# Getting started

## 1. Install

### Windows 10/11

1. Get the code: `git clone https://github.com/MayankPunghal/knowledge-search` (or **Code → Download ZIP** on
   GitHub and extract it).
2. In that folder run **`setup.bat --demo`** from Command Prompt — or double-click `setup.bat` and answer
   **Y** when asked whether to load the fictional sample documents. It also asks whether to install the
   optional summary model (answer **Y**, ~3.2 GB; if you skip the question, e.g. an unattended install, the app downloads it in the background on first start; **N** turns summaries off). Other options: `--no-demo`, `--summaries`,
   `--no-summaries`, `--no-start`, `--no-shortcut` (the PowerShell-style `-Demo`, `-NoStart` also work).

The setup window shows each step:

| Step | What happens | Needs admin rights? |
|---|---|---|
| uv | Installs [uv](https://docs.astral.sh/uv/), a Python package manager, into your user profile | No |
| Dependencies | uv downloads Python 3.13, the app's libraries and an embedded PostgreSQL + pgvector (~600 MB) | No |
| Models | Downloads the offline embedding model and reranker (~300 MB) into `data/models`; network drops are retried, and re-running setup reuses finished files | No |
| Summary model (optional) | Downloads llama.cpp's server (github.com) and a language model (Gemma 4 E2B, ~3.1 GB, huggingface.co) into `data/models/llm`; if it fails, setup continues without it | No |
| Database | Creates the local database in `data/pg` | No |
| Sample data | Optional fictional documents and demo users | No |
| Shortcut | Puts a **Knowledge Search** shortcut on the desktop | No |
| Start | Starts the app and opens <http://127.0.0.1:8000> | No |

Keep the black console window open while using the app; closing it stops the app.
Re-running `setup.bat` is safe (it skips what is already done).

### macOS / Linux

```bash
git clone https://github.com/MayankPunghal/knowledge-search && cd knowledge-search
./setup.sh            # --demo: sample data without asking, --summaries: install the summary model, --no-start
```

### Behind a corporate proxy

Setup needs internet access once. If downloads fail, set `HTTPS_PROXY` (and your company CA bundle via
`SSL_CERT_FILE`) before running setup. After setup the app runs offline.

## 2. First run

The browser opens a **Welcome** page: create the administrator account (username + a password of at least
10 characters). This page only works from the machine running the app and disappears once an admin exists.

## 3. Try it with the sample documents

If you loaded the sample data, three **fictional** collections exist: `engineering`, `finance` and a
confidential `hr`. Demo users: `alice`, `bob`, `hannah` (password `demo-password-<name>`).

| Search for | Expected |
|---|---|
| `ERR-PAY-4012` | **Evidence found** — exact-identifier match in the payments error-code reference |
| `POST /api/v2/invoices/{id}/void` | the "Void an invoice" section |
| `how do I undo a bad release of the orders service` | the Rollback section of the deployment runbook |
| `per diem for domestic travel` | **Conflicting evidence** — the 2024 and 2025 policies differ |
| `ERR-PAY-4013` | **No adequate evidence** — the code does not exist |
| `how many weeks of paid parental leave` | signed in as `bob`: nothing from HR; as `hannah` or admin: the HR policy |

Click **View passage** to see the passage in context and **Open source** to open the original file.

## 4. Test run with your own documents

1. Put 3–20 non-confidential files in a folder: `.md`, `.txt` (UTF-8), `.html`, `.docx`, `.pptx` or `.pdf` with selectable text.
2. Sign in as the administrator → **Users & access** → create a collection, e.g. `trial`.
3. **Documents** → choose collection `trial` → drag the files onto the drop zone. Each file shows
   `queued`; the table switches from `pending` to `indexed` within seconds to minutes (larger models are slower).
4. **Search** with three kinds of queries:
   - an exact term from a document (ID, product name, file name) → expect *evidence found*;
   - a question in your own words → expect the right passage at the top;
   - a question your documents cannot answer → expect *no adequate evidence*.
5. Edit a file and drop it again: only that file is re-indexed; text you removed is no longer found.
6. With the summary model installed: **Index status** shows "summaries by local model: N of M documents"
   (about 5–15 s per document, after indexing has finished). Search again: results now show a summary and
   topics, and file-name titles are replaced by clearer ones.

A whole folder: **choose a folder** next to *browse* uploads every supported file in it and its subfolders.
Command-line alternative: `uv run knowledge-search ingest trial path/to/folder --wait`.

## 5. Your website or intranet

Copy it with site2md first (see [the README](../README.md#search-a-website-or-intranet) and
[`site2md/README.md`](../site2md/README.md)), then upload the copy's folder (`site2md/output/<name>`) with
**choose a folder** into a collection only the intended readers can access. The copy has no access
control of its own.

Next: [User guide](user-guide.md) · [Admin guide](admin-guide.md)
