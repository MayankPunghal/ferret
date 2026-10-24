"""Background enrichment by a LOCAL language model: for every indexed document, a 2-sentence summary, a document
type from a fixed list (runbook, how-to, policy, ...), up to 6 topics and, when the document's own title is
vague ("Overview", "Home", a file name), a clearer title.

- Optional. The model is the one installed by setup (`knowledge-search download-llm`: llama.cpp's
  llama-server + a small GGUF model, started by the app on 127.0.0.1) or any OpenAI-compatible local
  endpoint (`KS_LLM_URL`: Ollama, LM Studio). Without one, nothing changes and nothing is sent anywhere.
- Never in the way: one document at a time, each as soon as it is indexed (while the others are still being
  indexed); the model server runs at the lowest CPU priority without busy-waiting, so indexing and search get
  the processor first (measured: indexing speed unchanged), and search never waits for it.
- Trust: summaries and topics are labelled as written by the model and are never cited as evidence.
  With "add a line about the whole document to every passage" on (context_chars), a document without its
  own description is re-indexed once so that line is the summary — measured: Kubernetes pages without
  descriptions, top result 96.4% -> 98.2% with Gemma 4 E2B summaries (eval/llm_summaries.py).
- Cost is per document (its opening text and headings), not per page: ~10-20 s on a 4-core CPU.
- Consistent topics: the model is shown the topics already used most in the document's collection and asked
  to reuse those names; a topic that differs from a known one only in case, hyphens or a plural takes the
  known spelling. The type and topics are labels for finding and filtering; they are not used in ranking
  (not measured to help yet).
- Hours: with the setting "enrich_hours" (e.g. 22-6) the model only works in that window, for machines
  that are busy during the day.
"""

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from psycopg import Connection
from psycopg.types.json import Jsonb

from . import db, runtime
from .parsing import GENERIC

log = logging.getLogger(__name__)

PROMPT_VERSION = 3
SOURCE_CHARS = 3000  # opening text given to the model (plus the headings outline)
MAX_ATTEMPTS = 3
CLAIM_MINUTES = 15
DOC_TYPES = {  # type: what it means (shown to the model)
    "runbook": "operational steps for a service: deploy, roll back, on-call",
    "how-to": "steps to do one task",
    "troubleshooting": "symptoms, causes and fixes; error codes and what to do",
    "reference": "facts to look up: an API, settings, a service's description, a schema",
    "concept": "explains an idea or how something works",
    "design": "architecture or a proposal for how to build something",
    "decision record": "a decision and why it was made",
    "policy": "rules people must follow: HR, expenses, security, compliance",
    "process": "a business workflow: who does what, in which order",
    "release notes": "what changed in a version",
    "incident report": "what went wrong, why, and follow-ups",
    "meeting notes": "notes or minutes of a meeting",
    "org chart": "who reports to whom, teams and roles",
    "onboarding": "getting started for new people",
    "presentation": "slides about a topic",
    "other": "none of these",
}
PROMPT = """You index internal technical documentation for search. Read the document below and reply with JSON:
{{"title": "<a short, specific title for the document>",
 "type": "<the type below that fits best>",
 "summary": "<2 sentences, at most 50 words: what the document is about and what it explains>",
 "topics": ["<up to 6 systems, services, components or concepts the document relies on or relates to>"]}}
Types: {types}
{known}
Document title: {title}{title_note}
{outline}
{text}"""
KNOWN_TOPICS = 40  # most used topics of the collection, shown to the model so it reuses their names


def vague_title(title: str) -> bool:
    """Titles that say nothing about the document (a clearer one written by the model is shown instead)."""
    t = " ".join(title.lower().replace("_", " ").split())
    return (t in GENERIC or re.fullmatch(r"[\w\- .()]+\.(pdf|docx?|pptx?|md|markdown|txt|html?)", t) is not None
            or re.fullmatch(r"(doc|document|file|scan|img|image|page|slide|untitled)?[\s\-#]*\d+", t) is not None)


def clearer_title(original: str, proposed: str) -> str | None:
    """The model's title, only when the original is vague and the proposal is not (nor the file name again)."""
    def norm(t):
        return re.sub(r"\.(pdf|docx?|pptx?|md|markdown|txt|html?)$", "", " ".join(t.lower().replace("_", " ").replace("-", " ").split()))
    if not proposed or not vague_title(original) or vague_title(proposed) or norm(proposed) == norm(original):
        return None
    return proposed


# --- the model ----------------------------------------------------------------------------------------------

class LLMError(Exception):
    pass


def chat(url: str, model: str, prompt: str, max_tokens: int = 300, timeout: float = 600, think: bool = False) -> str:
    body = {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens, "temperature": 0,
            "chat_template_kwargs": {"enable_thinking": think}}
    if not think:  # a JSON grammar would also constrain the reasoning
        body["response_format"] = {"type": "json_object"}
    if model:
        body["model"] = model
    req = urllib.request.Request(url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""
    except (urllib.error.URLError, OSError, KeyError, IndexError, ValueError) as e:
        raise LLMError(f"{type(e).__name__}: {e}"[:300]) from e


def parse_reply(raw: str) -> dict:
    """The model's JSON reply, checked and trimmed; raises LLMError when it is not usable."""
    raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    try:
        out = json.loads(m.group(0) if m else raw)
    except json.JSONDecodeError as e:
        raise LLMError("reply is not JSON") from e
    summary = " ".join(str(out.get("summary", "")).split())
    if not summary:
        raise LLMError("reply has no summary")
    topics = []
    for t in out.get("topics") or []:
        t = " ".join(str(t).split())[:60]
        if t and t.lower() not in {x.lower() for x in topics}:
            topics.append(t)
    kind = " ".join(str(out.get("type", "")).lower().replace("_", " ").split())
    kind = kind if kind in DOC_TYPES else next((t for t in DOC_TYPES if t.replace("-", " ") == kind.replace("-", " ")), "other")
    return {"title": " ".join(str(out.get("title", "")).split())[:120], "summary": summary[:500], "topics": topics[:6],
            "type": kind}


def topic_key(topic: str) -> str:
    """Spelling-insensitive key: case, hyphens/underscores/slashes and a plural last word do not matter."""
    words = re.sub(r"[-_/]+", " ", topic.lower()).split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def known_topics(conn: Connection, collection_id: int, limit: int = KNOWN_TOPICS) -> list[str]:
    """The collection's most used topics, each in its most common spelling."""
    rows = conn.execute(
        "SELECT t, count(*) AS n FROM documents d, jsonb_array_elements_text(d.enrichment->'topics') t "
        "WHERE d.collection_id = %s AND d.enrichment ? 'summary' GROUP BY t ORDER BY n DESC, t LIMIT 400",
        (collection_id,)).fetchall()
    best: dict[str, tuple[str, int]] = {}
    for r in rows:
        k = topic_key(r["t"])
        n = best.get(k, ("", 0))[1] + r["n"]
        best[k] = (best.get(k, (r["t"], 0))[0], n)  # first row of a key is its most common spelling
    return [t for t, _ in sorted(best.values(), key=lambda x: -x[1])[:limit]]


def align_topics(topics: list[str], known: list[str]) -> list[str]:
    """Use the known spelling of a topic the collection already has; drop duplicates."""
    by_key = {topic_key(t): t for t in known}
    out = []
    for t in topics:
        t = by_key.get(topic_key(t), t)
        if topic_key(t) not in {topic_key(x) for x in out}:
            out.append(t)
    return out


def source_text(conn: Connection, document_id) -> tuple[str, str, str]:
    """(title, headings outline, opening text) of the indexed version, from its passages."""
    rows = conn.execute(
        "SELECT doc_title, heading_path, text FROM chunks WHERE document_id = %s ORDER BY ordinal", (document_id,)
    ).fetchall()
    if not rows:
        return "", "", ""
    heads = list(dict.fromkeys(r["heading_path"] for r in rows if r["heading_path"]))[:40]
    text, n = [], 0
    for r in rows:  # passages overlap; the model only needs the gist
        if n >= SOURCE_CHARS:
            break
        text.append(r["text"])
        n += len(r["text"])
    outline = ("Sections: " + "; ".join(heads) + "\n") if heads else ""
    return rows[0]["doc_title"], outline, "\n\n".join(text)[:SOURCE_CHARS]


def shown(enrichment: dict | None, indexed_version_id) -> dict:
    """What results show: the summary, topics and clearer title, only if written for the indexed version."""
    e = enrichment or {}
    if not e.get("summary") or e.get("version_id") != str(indexed_version_id):
        return {"summary": None, "topics": [], "clearer_title": None, "doc_type": None}
    return {"summary": e["summary"], "topics": e.get("topics") or [], "clearer_title": e.get("title"),
            "doc_type": e.get("type")}


def in_hours(window: str, hour: int) -> bool:
    """Whether `hour` (0-23, local time) is inside "start-end" (e.g. "22-6" wraps past midnight); "" = always."""
    if not window:
        return True
    start, end = (int(x) for x in window.split("-"))
    return start <= hour < end if start < end else hour >= start or hour < end


# --- the model installed by setup (llama.cpp's llama-server, started by the app) ----------------------------

def llm_dir(settings) -> Path:
    return Path(settings.model_cache) / "llm"


def installed(settings, file: str = "") -> tuple[Path, Path] | None:
    """(llama-server executable, .gguf model) installed by `knowledge-search download-llm`, if present: the
    model `file`, else KS_LLM_MODEL, else the one selected by the last download."""
    d = llm_dir(settings)
    exe = next((p for p in (d / "bin").rglob("llama-server.exe" if sys.platform == "win32" else "llama-server")
                if p.is_file()), None) if (d / "bin").is_dir() else None
    chosen = file or settings.llm_model or ((d / "selected.txt").read_text().strip() if (d / "selected.txt").is_file() else "")
    model = d / chosen if chosen else next(iter(sorted(d.glob("*.gguf"))), None)
    return (exe, model) if exe and model and model.is_file() else None


class LocalServer:
    """llama-server on a free localhost port, for as long as the app runs."""

    def __init__(self, exe: Path, model: Path, threads: int, log_dir: Path):
        # absolute: the server runs in its own folder (KS_MODEL_CACHE is relative by default)
        self.exe, self.model, self.threads, self.log_dir = exe.resolve(), model.resolve(), threads, log_dir
        self.proc: subprocess.Popen | None = None
        self.url = ""

    def start(self, wait: float = 300) -> str:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.log_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)  # the release's shared libraries sit next to the executable
        for var in ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH"):
            env[var] = os.pathsep.join(filter(None, [str(self.exe.parent), env.get(var)]))
        self._log = open(self.log_dir / "llm-server.log", "ab")  # noqa: SIM115 - the server writes to it until stop()
        # -t: llama.cpp threads busy-wait, so only the cores we leave it (measured: 4 threads on 2 free cores
        # made one request 6x slower). One request at a time; 8k context fits the prompt.
        # It runs while documents are being indexed, so: lowest CPU priority (nice 19 / idle class) and no
        # polling. Measured on 4 cores, embedding 151 passages: 17.5 s alone, 37.1 s with the model busy at
        # normal priority, 17.6 s at nice 19; a summary alone takes as long as before (16 s).
        nice = [] if sys.platform == "win32" or not shutil.which("nice") else ["nice", "-n", "19"]
        self.proc = subprocess.Popen(
            [*nice, str(self.exe), "-m", str(self.model), "--host", "127.0.0.1", "--port", str(port), "-c", "8192",
             "-np", "1", "-t", str(self.threads), "--poll", "0", "--jinja"],
            stdout=self._log, stderr=subprocess.STDOUT, cwd=str(self.exe.parent), env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "IDLE_PRIORITY_CLASS", 0))
        self.url = f"http://127.0.0.1:{port}/v1"
        end = time.monotonic() + wait
        while time.monotonic() < end and self.proc.poll() is None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as r:
                    if r.status == 200:
                        return self.url
            except OSError:
                pass
            time.sleep(1)
        self.stop()
        raise LLMError("the local language model did not start; see logs/llm-server.log")

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None
        if getattr(self, "_log", None):
            self._log.close()


# --- the background job -------------------------------------------------------------------------------------

# Indexed documents whose summary is missing, or was written for another version, model or prompt, or failed
# fewer than MAX_ATTEMPTS times.
PENDING = ("d.status = 'indexed' AND d.indexed_version_id IS NOT NULL "
           "AND (d.enrichment IS NULL OR d.enrichment->>'version_id' IS DISTINCT FROM d.indexed_version_id::text "
           "  OR d.enrichment->>'model' IS DISTINCT FROM %(model)s "
           "  OR (d.enrichment->>'prompt')::int IS DISTINCT FROM %(prompt)s "
           "  OR (d.enrichment ? 'error' AND (d.enrichment->>'attempts')::int < %(max)s)) ")


def _args(model: str, **kw) -> dict:
    return {"model": model, "prompt": PROMPT_VERSION, "max": MAX_ATTEMPTS, **kw}


def status(conn: Connection, model: str, collections: list[int] | None = None) -> dict:
    """Counts of indexed documents summarised / waiting / given up on, within `collections` (None = all)."""
    scope = "" if collections is None else "AND d.collection_id = ANY(%(cols)s) "
    args = _args(model, cols=collections)
    n = conn.execute(
        "SELECT count(*) FILTER (WHERE " + PENDING + ") AS pending, "
        "count(*) FILTER (WHERE NOT (" + PENDING + ") AND d.enrichment ? 'error') AS failed, "
        "count(*) FILTER (WHERE NOT (" + PENDING + ") AND d.enrichment ? 'summary') AS summarised "
        "FROM documents d WHERE d.status = 'indexed' AND d.indexed_version_id IS NOT NULL " + scope, args).fetchone()
    return {"summarised": n["summarised"], "pending": n["pending"], "failed": n["failed"]}


def describe(enricher, conn: Connection, enabled: bool, collections: list[int]) -> str:
    """One line for the status page."""
    if not enabled:
        return "off (Settings)"
    if enricher is None or not enricher.available:
        return enricher.state if enricher and enricher.state.startswith(("downloading", "error")) \
            else "no local language model installed"
    c = status(conn, enricher.model, collections)
    total = sum(c.values())
    failed = f", {c['failed']} could not be summarised" if c["failed"] else ""
    name = enricher.model or "local model"  # an external server (llm_url) may not name its model
    return f"{name}: {c['summarised']} of {total} documents{failed} · {enricher.state}"


class Enricher:
    def __init__(self, settings):
        self.settings = settings
        self.server: LocalServer | None = None
        self.url = settings.llm_url
        self.model = settings.llm_model
        self.state = "off"  # off | no model | starting | working | idle | error: ...
        self.stopping = False
        self.broken = ""  # the installed model server could not start: reported, not retried until restart
        self.download_failed = False  # the automatic download failed: retried at the next app start
        if not self.url:
            self._find_installed()

    def _find_installed(self, file: str = "") -> None:
        found = installed(self.settings, file)
        if found:
            threads = self.settings.llm_threads or max(1, (os.cpu_count() or 2) // 2)
            self.server = LocalServer(found[0], found[1], threads, Path(self.settings.data_dir) / "logs")
            self.model = found[1].name
        else:
            self.state = "no model"

    def _download(self, name: str = "") -> None:
        """No model yet (or another one chosen in Settings) and summaries are on: fetch it in the background,
        like the embedding model on first use (setup can fetch it beforehand; KS_OFFLINE=true never downloads)."""
        name = name or DEFAULT_LLM
        _, file, note = LLMS[name]
        self.state = f"downloading the summary model {file} ({note.split(',')[0]}, one-time)"
        log.warning("document summaries: %s", self.state)
        try:
            download(self.settings, name)
        except Exception as e:  # noqa: BLE001 - network errors come in many types; search is unaffected
            self.download_failed = True
            self.state = f"error: the summary model could not be downloaded ({type(e).__name__}: {e})"[:300]
            log.error("%s; retried at the next start", self.state)
            return
        self._find_installed()  # download() selected it

    def _use(self, name: str) -> None:
        """Switch to the model chosen in Settings (documents written by another model are then rewritten)."""
        file = LLMS[name][1]
        if self.server and self.server.model.name == file:
            return
        if installed(self.settings, file):
            self.stop_server()
            self.broken = ""
            self._find_installed(file)
        elif not self.settings.offline and not self.download_failed:  # else the current model carries on
            self.stop_server()
            self._download(name)

    @property
    def available(self) -> bool:
        return bool(self.url or self.server)

    def _ensure_server(self) -> bool:
        if self.server and (self.server.proc is None or self.server.proc.poll() is not None):
            self.state = "starting"
            try:
                self.url = self.server.start()
            except (OSError, LLMError) as e:
                self.broken = f"error: the local language model could not start ({e})"[:300]
                log.error("%s; see logs/llm-server.log. Summaries are paused until the app restarts.", self.broken)
                return False
        return True

    def run_once(self) -> bool:
        """Summarise one document. False when there is nothing to do."""
        if not self.settings.llm_url:
            with db.pool().connection() as conn:
                rt = runtime.load(conn)
            if rt.enrich_enabled and rt.enrich_model:  # outside the connection: a download takes minutes
                self._use(rt.enrich_model)
            elif rt.enrich_enabled and not self.available and not self.download_failed and not self.settings.offline:
                self._download()
                return self.available
        with db.pool().connection() as conn:
            rt = runtime.load(conn)
            conn.commit()
            if self.broken:
                self.state = self.broken
                return False
            if not rt.enrich_enabled or not self.available:
                self.state = "off" if self.available else (self.state if self.download_failed else "no model")
                return False
            if not in_hours(rt.enrich_hours, time.localtime().tm_hour):
                self.state = "waiting for its hours ({}:00 to {}:00)".format(*rt.enrich_hours.split("-"))
                self.stop_server()
                return False
            if not self._ensure_server():
                self.state = self.broken
                return False
            with conn.transaction():
                doc = conn.execute(
                    "SELECT d.id, d.title, d.metadata, d.collection_id, d.indexed_version_id, d.enrichment FROM documents d WHERE "
                    + PENDING + "AND (d.enrich_claimed_at IS NULL OR d.enrich_claimed_at < now() - "
                    "make_interval(mins => %(claim)s)) ORDER BY d.created_at, d.id LIMIT 1 FOR UPDATE SKIP LOCKED",
                    _args(self.model, claim=CLAIM_MINUTES)).fetchone()
                if not doc:
                    self.state = "idle"
                    return False
                conn.execute("UPDATE documents SET enrich_claimed_at = now() WHERE id = %s", (doc["id"],))
            title, outline, text = source_text(conn, doc["id"])
            known = known_topics(conn, doc["collection_id"])
            conn.commit()
            base = {"version_id": str(doc["indexed_version_id"]), "model": self.model, "prompt": PROMPT_VERSION}
            t = time.perf_counter()
            try:
                self.state = "working"
                note = (" (not descriptive: write a specific title of at most 8 words from the content, not a file name)"
                        if vague_title(doc["title"]) else "")
                hint = ("Reuse these topic names when one fits (they are already used in this collection): "
                        + "; ".join(known) + "\n") if known else ""
                out = parse_reply(chat(self.url, self.model, PROMPT.format(
                    title=title or doc["title"], title_note=note, outline=outline, text=text,
                    types="; ".join(f"{k} ({v})" for k, v in DOC_TYPES.items()), known=hint)))
            except LLMError as e:
                if self.stopping:  # the app is closing: not the document's fault
                    conn.execute("UPDATE documents SET enrich_claimed_at = NULL WHERE id = %s", (doc["id"],))
                    conn.commit()
                    return False
                prev = doc["enrichment"] or {}
                attempts = (prev.get("attempts", 0) if "error" in prev and prev.get("version_id") == base["version_id"]
                            else 0) + 1
                log.warning("summary failed for document %s (attempt %d): %s", doc["id"], attempts, e)
                self.state = f"error: {e}"[:200]
                self._store(conn, doc["id"], {**base, "error": str(e)[:300], "attempts": attempts})
                return True
            own = doc["metadata"].get("description") or doc["metadata"].get("summary")
            better = clearer_title(doc["title"], out["title"])
            prev = doc["enrichment"] or {}
            # Only the prompt changed (a new label, say): keep the summary already in this version's passage
            # embeddings, so the whole archive is not re-indexed for it.
            keep = prev.get("summary") and prev.get("version_id") == base["version_id"] and prev.get("model") == self.model
            self._store(conn, doc["id"], {**base, "summary": prev["summary"] if keep else out["summary"],
                                          "title": prev.get("title") if keep else better, "type": out["type"],
                                          "topics": align_topics(out["topics"], known),
                                          "seconds": round(time.perf_counter() - t, 1)})
            if rt.context_chars and not own and not keep:  # put the summary into this document's passage embeddings
                from .ingest import requeue

                requeue(conn, doc["id"])
            return True

    @staticmethod
    def _store(conn: Connection, document_id, enrichment: dict) -> None:
        with conn.transaction():
            conn.execute("UPDATE documents SET enrichment = %s, enrich_claimed_at = NULL WHERE id = %s",
                         (Jsonb(enrichment), document_id))

    def stop(self) -> None:
        """Stop the model server now, even mid-request (the document is picked up again next time)."""
        self.stopping = True
        self.stop_server()

    def stop_server(self) -> None:
        """Free the model's memory (outside its hours, or when the app closes); it starts again when needed."""
        if self.server and self.server.proc and self.server.proc.poll() is None:
            self.server.stop()

    def loop(self, stop: threading.Event, poll: float = 5.0) -> None:
        while not stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001 - keep the job alive; the error is logged and shown in status
                log.exception("summary job failed")
                self.state = "error: see the log"
                worked = False
            if not worked:
                stop.wait(poll)
        self.stop()


# --- download (setup --summaries / `knowledge-search download-llm`) ----------------------------------------

# The llama.cpp build the summaries were measured with; every platform's CPU build is published per release.
LLAMA_CPP_TAG = "b11836"
LLMS = {  # name: (Hugging Face repo, file, note); the notes are what the blind review and type check measured
    "gemma-4-e2b": ("unsloth/gemma-4-E2B-it-GGUF", "gemma-4-E2B-it-Q4_K_M.gguf",
                    "~3.1 GB, Apache-2.0; recommended: no wrong claims in the blind review, best document types"),
    "gemma-4-e4b": ("unsloth/gemma-4-E4B-it-GGUF", "gemma-4-E4B-it-Q4_K_M.gguf",
                    ("~5 GB, needs ~9 GB of memory, Apache-2.0; for a server: summaries as good as E2B, document types "
                     "a little better (10 vs 8 of 14 pages), 2.3x slower")),
    "qwen3.5-2b": ("unsloth/Qwen3.5-2B-GGUF", "Qwen3.5-2B-Q4_K_M.gguf",
                   "~1.3 GB, Apache-2.0; as accurate in the blind review, about as fast; document types worse (7 vs 8 of 14 pages)"),
    "qwen3.5-0.8b": ("unsloth/Qwen3.5-0.8B-GGUF", "Qwen3.5-0.8B-Q4_K_M.gguf",
                     "~0.5 GB, Apache-2.0; about 3x faster, more mistakes (3 wrong claims in 9 pages)"),
}
DEFAULT_LLM = "gemma-4-e2b"  # blind review: no wrong claims in 9 pages; Qwen3.5-0.8B made 3 (eval/results/blind-review.json)


def asset_name(tag: str = LLAMA_CPP_TAG) -> str:
    """File name of llama.cpp's CPU build for this platform in release `tag`."""
    import platform

    arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
    if sys.platform == "win32":
        return f"llama-{tag}-bin-win-cpu-{arch}.zip"
    return f"llama-{tag}-bin-{'macos' if sys.platform == 'darwin' else 'ubuntu'}-{arch}.tar.gz"


def unpack(archive: Path, dest: Path) -> None:
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    if archive.suffix == ".zip":
        shutil.unpack_archive(str(archive), str(dest))
    else:
        shutil.unpack_archive(str(archive), str(dest), filter="data")
    for p in dest.rglob("llama-server*"):
        p.chmod(p.stat().st_mode | 0o111)


def download(settings, name: str = DEFAULT_LLM, select: bool = True) -> Path:
    """Fetch the llama.cpp server (once) and the model `name`; `select` makes it the one setup installed (used
    when Settings names none). Returns the model file."""
    from huggingface_hub import hf_hub_download

    repo, file, _ = LLMS[name]
    d = llm_dir(settings)
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "bin" / LLAMA_CPP_TAG).is_dir():
        url = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_CPP_TAG}/{asset_name()}"
        archive = d / asset_name()
        print("downloading", url, flush=True)
        req = urllib.request.Request(url, headers={"User-Agent": "knowledge-search"})
        with urllib.request.urlopen(req, timeout=120) as r, open(archive, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        unpack(archive, d / "bin" / "partial")
        (d / "bin" / "partial").rename(d / "bin" / LLAMA_CPP_TAG)  # complete only once fully unpacked
        archive.unlink()
        for old in (d / "bin").iterdir():  # earlier builds
            if old.name != LLAMA_CPP_TAG:
                shutil.rmtree(old, ignore_errors=True)
    if not (d / file).is_file():  # huggingface_hub only warns when the disk is too small
        from huggingface_hub import get_hf_file_metadata, hf_hub_url

        size, free = get_hf_file_metadata(hf_hub_url(repo, file)).size or 0, shutil.disk_usage(d).free
        if size and free < size + (200 << 20):
            raise OSError(f"not enough disk space for {file}: it needs {size / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
    print(f"downloading {repo}/{file}", flush=True)
    path = Path(hf_hub_download(repo, file, local_dir=d))
    if select:
        (d / "selected.txt").write_text(file)
    return path
