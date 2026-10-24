"""Build a 600+ page REAL technical documentation corpus from the Kubernetes docs (CC BY 4.0) for stress
tests and the labelled evaluation in eval/corpus_eval.py.

    uv run python eval/make_k8s_corpus.py            # -> data/corpora/kubernetes/*.md (needs git + network)
    uv run ferret ingest kubernetes data/corpora/kubernetes --wait   # then search in the UI

Pages: concepts, tasks, tutorials, setup, glossary and a few reference sections of kubernetes/website at a
pinned commit, so results are reproducible. Hugo shortcodes are reduced to plain text, front matter to the
title, and every page gets a unique file name equal to the last part of its URL (`/docs/.../deployment/` →
`deployment.md`), so the site's own links become graph edges. Source: https://github.com/kubernetes/website
(documentation licensed CC BY 4.0, © The Kubernetes Authors); not committed to this repository.
"""

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "https://github.com/kubernetes/website"
COMMIT = "6c42e266fcc1f0e0edd4805baea904e1b3cbaf9c"
SECTIONS = ["concepts", "tasks", "tutorials", "setup", "reference/glossary", "reference/access-authn-authz",
            "reference/node", "reference/networking", "reference/labels-annotations-taints"]
MIN_CHARS = 300


def fetch(src: Path) -> None:
    if not (src / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--sparse", "--no-checkout", REPO, str(src)],
                       check=True)
    subprocess.run(["git", "-C", str(src), "sparse-checkout", "set",
                    *[f"content/en/docs/{s}" for s in SECTIONS]], check=True)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", COMMIT], check=True)


def clean(text: str, description: bool = True) -> tuple[str, str]:
    """(title, markdown body) of one Hugo page; `description` keeps the page's one-line description as the
    first paragraph (intranet pages rarely have one: --no-description simulates them)."""
    title, body = "", text
    m = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    if m:
        fm, body = m.group(1), text[m.end():]
        t = re.search(r"(?m)^title:\s*[\"']?(.+?)[\"']?\s*$", fm)
        title = t.group(1) if t else ""
        d = re.search(r"(?ms)^(?:description|short_description):\s*>?-?\s*\n?(.+?)(?=^\S|\Z)", fm)
        if d and description:
            body = " ".join(d.group(1).split()) + "\n\n" + body
    # {{< glossary_tooltip text="Pods" term_id="pod" >}} -> Pods ; {{< glossary_tooltip term_id="pod" >}} -> pod
    body = re.sub(r"\{\{<\s*glossary_tooltip\s+(?:text=\"([^\"]*)\"\s+)?term_id=\"([^\"]*)\"(?:\s+text=\"([^\"]*)\")?\s*>\}\}",
                  lambda g: g.group(1) or g.group(3) or g.group(2).replace("-", " "), body)
    body = re.sub(r"\{\{[<%]\s*(?:code_sample|codenew)[^}]*file=\"([^\"]+)\"[^}]*[>%]\}\}", r"(example file: \1)", body)
    body = re.sub(r"\{\{[<%]\s*/?\s*(note|caution|warning)\s*[>%]\}\}", lambda g: "" if "/" in g.group(0) else f"{g.group(1).title()}: ", body)
    body = re.sub(r"\{\{[<%].*?[>%]\}\}", "", body, flags=re.DOTALL)  # remaining shortcodes
    body = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return title, body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "corpora" / "kubernetes"))
    ap.add_argument("--src", default=str(ROOT / "data" / "src" / "k8s-website"))
    ap.add_argument("--no-description", action="store_true", help="drop the pages' curated descriptions")
    a = ap.parse_args()
    src, out = Path(a.src), Path(a.out)
    fetch(src)
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.md"):
        old.unlink()
    docs = src / "content" / "en" / "docs"
    used, n, skipped = set(), 0, 0
    for section in SECTIONS:
        for f in sorted((docs / section).rglob("*.md")):
            text = f.read_text(encoding="utf-8")
            title, body = clean(text)
            if len(body) < MIN_CHARS or not title:  # same page set with or without descriptions
                skipped += 1
                continue
            if a.no_description:
                body = clean(text, False)[1]
            name = f.parent.name if f.stem in ("_index", "index") else f.stem
            if name in used:  # keep names unique; the first page keeps the URL name links point to
                name = f"{f.parent.name}-{name}"
            used.add(name)
            (out / f"{name}.md").write_text(f"---\ntitle: {json.dumps(title)}\n---\n\n# {title}\n\n{body}\n", encoding="utf-8")
            n += 1
    (out / "NOTICE.txt").write_text(f"Kubernetes documentation, {REPO} at {COMMIT}.\n"
                                    "Licensed CC BY 4.0, (c) The Kubernetes Authors. Converted for testing.\n")
    print(f"{n} pages -> {out} ({skipped} short/untitled pages skipped)")


if __name__ == "__main__":
    main()
