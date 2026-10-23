"""Build the image test set for the vision-model trial: pictures whose text (and, for diagrams, whose arrows)
are known exactly, so reading them can be scored. Public or made-up content only.

    uv run --group eval --with playwright --with pillow python eval/image_set.py [--chromium PATH]

Writes data/images-eval/<name>.png and truth.json ({name: {"kind", "text", "edges"}}). Kinds:
  docs        a documentation page (Kubernetes docs, rendered as a docs site would)
  ui          an app screen, as in a user manual (forms, tables, buttons)
  table       a holiday calendar as a table, and as a designed banner
  orgchart    boxes and lines: names, titles and who reports to whom
  flowchart   an approval workflow / incident process with arrows
  slide       a presentation slide
  scan        a PDF page printed and scanned (low resolution, slightly rotated, noisy)
  small       a manual screenshot shrunk to half size (small text)
"""

import argparse
import io
import json
import random
import re
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "images-eval"
CSS = ("body{font-family:Arial,Helvetica,sans-serif;margin:24px;color:#222;background:#fff}"
       "table{border-collapse:collapse}td,th{border:1px solid #bbb;padding:6px 10px;text-align:left}"
       "th{background:#f0f3f7}code,pre{font-family:Consolas,monospace;background:#f5f5f5}pre{padding:8px}")


def docs_pages(n=6):
    """Kubernetes docs pages: the first ~1,400 characters of text, rendered as a docs site."""
    md = MarkdownIt()
    names = ["configmap.md", "ingress.md", "rbac-good-practices.md", "kubectl-reference.md", "pod-lifecycle.md",
             "secret.md", "assign-pod-node.md", "service.md"]
    out = []
    for name in names:
        p = ROOT / "data" / "corpora" / "kubernetes" / name
        if not p.exists():
            continue
        text = re.sub(r"(?s)^---\n.*?\n---\n", "", p.read_text(encoding="utf-8"))
        text = re.sub(r"\{\{<[^>]*>\}\}|\{\{%[^%]*%\}\}", "", text)  # Hugo shortcodes
        cut = text[:1400].rsplit("\n", 1)[0]
        body = md.render(cut)
        out.append((f"docs-{p.stem}", "docs", f"<html><head><style>{CSS} h1{{color:#326ce5}}</style></head>"
                    f"<body style='width:900px'>{body}</body></html>", []))
        if len(out) == n:
            break
    return out


def ui_screens():
    leave = """<div style='width:820px;border:1px solid #ccc;border-radius:6px'>
<div style='background:#1f4e79;color:#fff;padding:10px 16px;font-size:18px'>HRIS &rsaquo; Leave &rsaquo; Apply Leave</div>
<div style='padding:16px'><p><b>Leave Type</b> <span style='border:1px solid #999;padding:4px 40px 4px 8px'>Sick Leave</span>
&nbsp; <b>Balance:</b> 9.5 days</p>
<p><b>From</b> <span style='border:1px solid #999;padding:4px 8px'>14-Nov-2026</span> &nbsp; <b>To</b>
<span style='border:1px solid #999;padding:4px 8px'>16-Nov-2026</span> &nbsp; <b>Half day</b> &#9744;</p>
<p><b>Reason</b></p><div style='border:1px solid #999;height:50px;padding:6px'>Fever, doctor's certificate attached</div>
<p><b>Approver:</b> Priya Raman (Engineering Manager)</p>
<span style='background:#2e7d32;color:#fff;padding:8px 18px;border-radius:4px'>Submit</span>
<span style='background:#ddd;padding:8px 18px;border-radius:4px;margin-left:8px'>Cancel</span></div></div>"""
    attendance = """<h2>My Attendance - October 2026</h2><table>
<tr><th>Date</th><th>Check-in</th><th>Check-out</th><th>Hours</th><th>Status</th></tr>
<tr><td>01-Oct-2026</td><td>09:12</td><td>18:05</td><td>8:53</td><td>Present</td></tr>
<tr><td>02-Oct-2026</td><td>-</td><td>-</td><td>0:00</td><td>Holiday (Gandhi Jayanti)</td></tr>
<tr><td>05-Oct-2026</td><td>09:47</td><td>17:30</td><td>7:43</td><td>Short day</td></tr>
<tr><td>06-Oct-2026</td><td>-</td><td>-</td><td>0:00</td><td>Sick Leave</td></tr>
<tr><td>07-Oct-2026</td><td>08:58</td><td>18:20</td><td>9:22</td><td>Present</td></tr></table>
<p style='color:#b00'>Regularize: click a date with status "Short day" and choose "Raise request".</p>"""
    profile = """<div style='width:760px;font-size:15px'><h2 style='margin:0'>Employee Profile</h2>
<p>Employee ID: <b>SF-10482</b> &nbsp; Department: <b>Cloud Engineering</b> &nbsp; Location: <b>Noida</b></p>
<p>Reporting Manager: <b>Arjun Mehta</b> &nbsp; Date of Joining: <b>03-Jan-2024</b></p>
<div style='display:flex;gap:10px'><span style='border:1px solid #1f4e79;padding:6px 12px'>Personal</span>
<span style='border:1px solid #1f4e79;padding:6px 12px;background:#1f4e79;color:#fff'>Bank Details</span>
<span style='border:1px solid #1f4e79;padding:6px 12px'>Documents</span></div>
<p>Bank: HDFC Bank &nbsp; IFSC: HDFC0001234 &nbsp; PF UAN: 100912345678</p>
<p style='font-size:13px;color:#555'>To change bank details, upload a cancelled cheque under Documents and notify payroll@example.com.</p></div>"""
    approvals = """<div style='width:800px'><h2>Pending Approvals (3)</h2>
<div style='border-left:4px solid #f9a825;padding:6px 12px;margin:8px 0'>Rahul Verma - Earned Leave - 2 days - 20-Nov-2026 <span style='float:right'>[Approve] [Reject]</span></div>
<div style='border-left:4px solid #f9a825;padding:6px 12px;margin:8px 0'>Neha Gupta - Work From Home - 1 day - 18-Nov-2026 <span style='float:right'>[Approve] [Reject]</span></div>
<div style='border-left:4px solid #f9a825;padding:6px 12px;margin:8px 0'>Sam Wilson - Comp Off - 1 day - 17-Nov-2026 <span style='float:right'>[Approve] [Reject]</span></div>
<p>Requests not acted on within 3 working days are escalated to HR Operations.</p></div>"""
    return [(f"ui-{n}", "ui", f"<html><head><style>{CSS}</style></head><body>{b}</body></html>", [])
            for n, b in [("apply-leave", leave), ("attendance", attendance), ("profile", profile),
                         ("approvals", approvals)]]


HOLIDAYS = [("01-Jan-2026", "Thursday", "New Year's Day"), ("26-Jan-2026", "Monday", "Republic Day"),
            ("04-Mar-2026", "Wednesday", "Holi"), ("03-Apr-2026", "Friday", "Good Friday"),
            ("15-Aug-2026", "Saturday", "Independence Day"), ("02-Oct-2026", "Friday", "Gandhi Jayanti"),
            ("20-Oct-2026", "Tuesday", "Dussehra"), ("08-Nov-2026", "Sunday", "Diwali"),
            ("25-Dec-2026", "Friday", "Christmas Day")]


def tables():
    rows = "".join(f"<tr><td>{d}</td><td>{w}</td><td>{h}</td></tr>" for d, w, h in HOLIDAYS)
    plain = f"<h2>India Holiday Calendar 2026</h2><table><tr><th>Date</th><th>Day</th><th>Holiday</th></tr>{rows}</table>"
    cards = "".join(f"<div style='background:#fff;border-radius:10px;padding:10px;width:180px;margin:6px;"
                    f"display:inline-block;vertical-align:top'><div style='font-size:22px;color:#c62828'><b>{d[:6]}</b>"
                    f"</div><div style='font-size:13px;color:#666'>{w}</div><div><b>{h}</b></div></div>"
                    for d, w, h in HOLIDAYS)
    banner = (f"<div style='width:980px;padding:20px;background:linear-gradient(135deg,#ff8a65,#7e57c2)'>"
              f"<div style='color:#fff;font-size:34px;font-weight:bold'>Holiday Calendar 2026 &middot; UK</div>"
              f"<div style='color:#fff;margin-bottom:10px'>Offices closed on these days</div>{cards}</div>")
    return [("table-holidays", "table", f"<html><head><style>{CSS}</style></head><body>{plain}</body></html>", []),
            ("table-holiday-banner", "table", f"<html><head><style>{CSS}</style></head><body>{banner}</body></html>", [])]


def _boxes_svg(nodes, edges, w, h, title):
    """nodes: {id: (x, y, line1, line2)}; edges: [(from, to)] drawn as arrows from box to box."""
    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' font-family='Arial'>",
             "<defs><marker id='a' markerWidth='10' markerHeight='8' refX='9' refY='4' orient='auto'>"
             "<path d='M0,0 L10,4 L0,8 z' fill='#444'/></marker></defs>",
             f"<text x='{w // 2}' y='30' font-size='22' text-anchor='middle' font-weight='bold'>{title}</text>"]
    for a, b in edges:
        (x1, y1, *_), (x2, y2, *_) = nodes[a], nodes[b]
        if abs(y2 - y1) > abs(x2 - x1):
            sy, ey = (y1 + 30, y2 - 30) if y2 > y1 else (y1 - 30, y2 + 30)
            parts.append(f"<line x1='{x1}' y1='{sy}' x2='{x2}' y2='{ey}' stroke='#444' stroke-width='2' marker-end='url(#a)'/>")
        else:
            sx, ex = (x1 + 85, x2 - 85) if x2 > x1 else (x1 - 85, x2 + 85)
            parts.append(f"<line x1='{sx}' y1='{y1}' x2='{ex}' y2='{y2}' stroke='#444' stroke-width='2' marker-end='url(#a)'/>")
    for x, y, l1, l2 in nodes.values():
        parts.append(f"<rect x='{x - 85}' y='{y - 30}' width='170' height='60' rx='8' fill='#e3f2fd' stroke='#1565c0'/>")
        parts.append(f"<text x='{x}' y='{y - 4 if l2 else y + 5}' font-size='14' text-anchor='middle' font-weight='bold'>{l1}</text>")
        if l2:
            parts.append(f"<text x='{x}' y='{y + 16}' font-size='12' text-anchor='middle'>{l2}</text>")
    return "".join(parts) + "</svg>"


def diagrams():
    org = {"ceo": (500, 90, "Meera Kapoor", "Chief Executive Officer"),
           "cto": (250, 220, "Daniel Brooks", "Chief Technology Officer"),
           "cfo": (750, 220, "Anita Desai", "Chief Financial Officer"),
           "eng": (130, 350, "Vikram Singh", "VP Engineering"), "qa": (370, 350, "Laura Chen", "Head of QA"),
           "fin": (650, 350, "Rohit Jain", "Finance Controller"), "hr": (870, 350, "Fatima Khan", "HR Director")}
    org_edges = [("ceo", "cto"), ("ceo", "cfo"), ("cto", "eng"), ("cto", "qa"), ("cfo", "fin"), ("cfo", "hr")]
    flow = {"emp": (110, 120, "Employee", "submits request"), "mgr": (340, 120, "Manager", "approves"),
            "hr": (570, 120, "HR Operations", "verifies"), "pay": (800, 120, "Payroll", "processes"),
            "rej": (340, 280, "Request rejected", "employee notified")}
    flow_edges = [("emp", "mgr"), ("mgr", "hr"), ("hr", "pay"), ("mgr", "rej")]
    inc = {"alert": (130, 110, "Alert fires", "PagerDuty"), "triage": (380, 110, "On-call triage", "within 15 min"),
           "sev1": (630, 110, "SEV1 bridge", "incident commander"), "fix": (630, 270, "Mitigate", "rollback or failover"),
           "pm": (380, 270, "Postmortem", "within 5 days")}
    inc_edges = [("alert", "triage"), ("triage", "sev1"), ("sev1", "fix"), ("fix", "pm")]

    def names(nodes, edges):
        return [(nodes[a][2], nodes[b][2]) for a, b in edges]

    def text(nodes, title):
        return "\n".join([title] + [f"{l1} {l2}" for _, _, l1, l2 in nodes.values()])

    page = "<html><body style='margin:0;background:#fff'>{}</body></html>"
    return [("orgchart-company", "orgchart", page.format(_boxes_svg(org, org_edges, 1000, 410, "Organisation Chart")),
             names(org, org_edges), text(org, "Organisation Chart")),
            ("flowchart-leave-approval", "flowchart", page.format(_boxes_svg(flow, flow_edges, 920, 340,
             "Leave Approval Workflow")), names(flow, flow_edges), text(flow, "Leave Approval Workflow")),
            ("flowchart-incident", "flowchart", page.format(_boxes_svg(inc, inc_edges, 780, 330,
             "Incident Response Process")), names(inc, inc_edges), text(inc, "Incident Response Process"))]


def slides():
    s1 = ("<div style='width:960px;height:540px;background:linear-gradient(160deg,#0d47a1,#26a69a);color:#fff;"
          "padding:40px;box-sizing:border-box'><div style='font-size:40px;font-weight:bold'>Welcome to IT Induction</div>"
          "<ul style='font-size:24px;line-height:1.6'><li>Your laptop is encrypted with BitLocker</li>"
          "<li>Connect to the office VPN with FortiClient</li><li>Passwords are kept in 1Password</li>"
          "<li>Raise IT tickets in Jira Service Management</li></ul>"
          "<div style='position:absolute;bottom:30px;font-size:16px'>IT Helpdesk &middot; ext. 4455</div></div>")
    s2 = ("<div style='width:960px;height:540px;background:#fafafa;padding:40px;box-sizing:border-box;"
          "font-family:Georgia'><div style='font-size:34px;color:#c62828'>Long Term Award (LTA)</div>"
          "<div style='display:flex;gap:40px;font-size:20px'><div><b>Eligibility</b><p>Employees with 3+ years of "
          "service on 1 April</p></div><div><b>Payout</b><p>10% of annual CTC, paid in two instalments</p></div>"
          "<div><b>Vesting</b><p>50% in June, 50% in December</p></div></div></div>")
    return [(f"slide-{n}", "slide", f"<html><body style='margin:0'>{b}</body></html>", [])
            for n, b in [("it-induction", s1), ("lta", s2)]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chromium", default="")
    a = ap.parse_args()
    from PIL import Image, ImageFilter
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    truth = {}
    items = docs_pages() + ui_screens() + tables() + diagrams() + slides()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(**({"executable_path": a.chromium} if a.chromium else {}))
        page = browser.new_page(viewport={"width": 1040, "height": 600})
        for item in items:
            name, kind, html, edges = item[:4]
            page.set_content(html)
            text = item[4] if len(item) > 4 else page.inner_text("body")
            page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
            truth[name] = {"kind": kind, "text": text, "edges": edges}
        browser.close()
    # a manual screenshot shrunk to half size: small text
    for src in ("ui-attendance", "docs-configmap"):
        if src in truth:
            im = Image.open(OUT / f"{src}.png")
            im.resize((im.width // 2, im.height // 2), Image.LANCZOS).save(OUT / f"small-{src}.png")
            truth[f"small-{src}"] = {**truth[src], "kind": "small"}
    # a printed and scanned page: the incident postmortem PDF (its text layer is the truth)
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(ROOT / "fixtures" / "corpus" / "engineering" / "incident-2025-031-postmortem.pdf"))
    pg = pdf[0]
    truth_text = pg.get_textpage().get_text_range()
    im = pg.render(scale=110 / 72).to_pil().convert("L").rotate(1.2, expand=True, fillcolor=255)
    rnd = random.Random(7)
    px = im.load()
    for _ in range(im.width * im.height // 60):  # speckle
        x, y = rnd.randrange(im.width), rnd.randrange(im.height)
        px[x, y] = rnd.choice((0, 90, 160))
    buf = io.BytesIO()
    im.filter(ImageFilter.GaussianBlur(0.6)).save(buf, "JPEG", quality=55)
    Image.open(buf).save(OUT / "scan-postmortem.png")
    truth["scan-postmortem"] = {"kind": "scan", "text": truth_text, "edges": []}
    (OUT / "truth.json").write_text(json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(truth), "images ->", OUT)


if __name__ == "__main__":
    main()
