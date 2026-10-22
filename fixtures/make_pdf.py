"""Generate the fictional multi-page fixture PDF with a real text layer (no dependencies).

Usage: python fixtures/make_pdf.py  ->  writes fixtures/corpus/engineering/incident-2025-031-postmortem.pdf
Also importable: make_pdf(pages: list[list[str]], title) -> bytes  (used by tests).
"""

from pathlib import Path

PAGES = [
    [
        "Incident 2025-031 Postmortem: Checkout outage",
        "",
        "Summary: On 14 March 2025 checkout was unavailable for 47 minutes. Customers saw",
        "HTTP 503 errors when placing orders. The incident was classified SEV1.",
        "",
        "Root cause: orders-svc v3.8.1 exhausted its database connection pool after a",
        "change that held connections open while calling the payments service. The pool",
        "maximum was 20 connections per pod, and slow payment authorizations kept every",
        "connection busy.",
    ],
    [
        "Resolution and follow-up actions",
        "",
        "The on-call engineer rolled back to v3.8.0, which restored checkout. The fix,",
        "released in orders-svc v3.8.2, releases the connection before calling payments",
        "and raises the pool maximum to 40 connections per pod.",
        "",
        "Action items: add an alert on connection pool saturation above 80 percent;",
        "add a load test for slow upstream dependencies; document pool sizing in the",
        "deployment runbook.",
    ],
]


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(pages: list[list[str]], title: str = "") -> bytes:
    objs: list[bytes] = []
    n_pages = len(pages)
    # 1 catalog, 2 pages, 3 font, then (page, content) pairs, then info
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n_pages))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i, lines in enumerate(pages):
        content = "BT /F1 11 Tf 14 TL 60 780 Td " + " ".join(f"({_esc(line)}) Tj T*" for line in lines) + " ET"
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Resources << /Font << /F1 3 0 R >> >> "
            f"/Contents {5 + 2 * i} 0 R >>".encode()
        )
        data = content.encode("latin-1")
        objs.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
    objs.append(f"<< /Title ({_esc(title)}) >>".encode())
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


if __name__ == "__main__":
    p = Path(__file__).parent / "corpus" / "engineering" / "incident-2025-031-postmortem.pdf"
    p.write_bytes(make_pdf(PAGES, "Incident 2025-031 Postmortem: Checkout outage"))
    print("wrote", p)
