"""Generate a FICTIONAL, interlinked platform wiki plus labelled multi-hop questions (graph evaluation).

    uv run python eval/make_wiki.py OUT_DIR          # writes OUT_DIR/wiki/*.md and OUT_DIR/queries.jsonl

The wiki imitates generated codebase/architecture wikis (Graphify, codebase-documenter): one page per
service with the same sections (Dependencies, Ownership, Runtime, Failure behaviour), workflow pages that
link the services they use, front-matter `depends_on` / `part_of` relations and `[[wiki links]]`. Every
service page has the same section types, so "who owns X" passages are hard negatives for each other.

Question classes, each labelled with the *set* of passages (file#anchor) needed to answer it:
  single     one attribute of one named service              (1 passage)
  deps       an attribute of the services a named service depends on (2-3 passages, none name the service)
  workflow   an attribute of every service in a named workflow       (3-4 passages)
  impact     owners of the services that depend on a named service   (2-4 passages)
  none       the same templates for services/workflows that do not exist (should abstain)
Deterministic (fixed seed). All names, people and numbers are invented.
"""

import json
import random
import sys
from pathlib import Path

SEED = 7
SERVICES = [  # (name, purpose)
    ("Ledger", "writes double-entry records for every money movement"),
    ("Identity", "issues and verifies user and service credentials"),
    ("Audit Log", "stores tamper-evident audit events"),
    ("Feature Flags", "serves feature toggles and percentage rollouts"),
    ("Currency", "converts amounts between currencies using daily reference rates"),
    ("Geo", "resolves addresses, regions and delivery zones"),
    ("Email", "renders and sends transactional email"),
    ("SMS", "sends one-time codes and delivery text messages"),
    ("Scheduler", "runs delayed and recurring jobs"),
    ("Media", "stores product images and generates thumbnails"),
    ("Tax", "calculates sales tax and VAT per jurisdiction"),
    ("Fraud", "scores payments and accounts for fraud risk"),
    ("Notifications", "fans out user notifications to email, SMS and push"),
    ("Catalog", "holds products, variants and their attributes"),
    ("Inventory", "tracks stock levels per warehouse"),
    ("Pricing", "computes list prices, discounts and price rules"),
    ("Sessions", "manages browser and mobile sessions"),
    ("Payments", "authorizes and captures card payments with the card processor"),
    ("Invoicing", "creates invoices and credit notes"),
    ("Coupons", "validates and redeems promotion codes"),
    ("Search", "indexes the catalog for storefront search"),
    ("Recommendations", "suggests related products"),
    ("Reviews", "collects and moderates product reviews"),
    ("Shipping", "books carriers and produces shipping labels"),
    ("Warehouse", "plans picking and packing in the warehouses"),
    ("Orders", "accepts orders from the storefront and reserves stock"),
    ("Refunds", "issues full and partial refunds"),
    ("Returns", "handles return requests and return labels"),
    ("Subscriptions", "renews recurring subscriptions"),
    ("Loyalty", "awards and redeems loyalty points"),
    ("Support Desk", "routes customer tickets to support agents"),
    ("Reporting", "builds daily finance and operations reports"),
    ("Exports", "produces CSV and accounting exports"),
    ("Webhooks", "delivers events to merchant endpoints"),
    ("Rate Limiter", "enforces request quotas per client"),
    ("Forecasting", "predicts demand per product and warehouse"),
]
TEAMS = ["Atlas", "Borealis", "Cobalt", "Driftwood", "Ember", "Fjord", "Granite", "Harbor", "Ion", "Juniper",
         "Kestrel", "Lumen"]
PEOPLE = ["Priya Natarajan", "Tomasz Wielgus", "Amara Okafor", "Lena Fischer", "Diego Ramos", "Hana Sato",
          "Marcus Bell", "Ines Duarte", "Oskar Lind", "Yara Haddad", "Ravi Menon", "Chloe Martin", "Kwame Asante",
          "Sofia Rossi", "Jonas Berg", "Mei Chen", "Arjun Kapoor", "Elif Demir", "Noah Fischer", "Zara Ahmed"]
STORES = ["PostgreSQL", "Redis", "Cassandra", "MySQL", "Elasticsearch", "S3 bucket"]
REGIONS = ["eu-west-1", "eu-central-1", "us-east-1", "ap-south-1"]
REASONS = ["to {v} for each request", "whenever it needs to {v}", "to {v}"]
VERBS = {  # what a caller uses a dependency for
    "Ledger": "record the money movement", "Identity": "verify the caller's token", "Audit Log": "record an audit event",
    "Feature Flags": "check rollout flags", "Currency": "convert amounts", "Geo": "resolve the delivery zone",
    "Email": "send a confirmation email", "SMS": "send a text message", "Scheduler": "schedule retries",
    "Media": "fetch product images", "Tax": "calculate tax", "Fraud": "score the risk",
    "Notifications": "notify the customer", "Catalog": "look up product details", "Inventory": "check stock",
    "Pricing": "get the current price", "Sessions": "load the session", "Payments": "take the payment",
    "Invoicing": "issue an invoice", "Coupons": "validate a promotion code", "Search": "query the search index",
    "Recommendations": "fetch suggestions", "Reviews": "load ratings", "Shipping": "book a carrier",
    "Warehouse": "plan picking", "Orders": "look up the order", "Refunds": "issue the refund",
    "Returns": "open the return", "Subscriptions": "check the subscription", "Loyalty": "award points",
    "Support Desk": "open a ticket", "Reporting": "publish figures", "Exports": "export the records",
    "Webhooks": "notify merchants", "Rate Limiter": "check the client's quota", "Forecasting": "read demand forecasts",
}
WORKFLOWS = {
    "Checkout": ["Sessions", "Pricing", "Orders", "Payments"],
    "Refund": ["Orders", "Refunds", "Payments", "Ledger"],
    "Returns": ["Returns", "Shipping", "Refunds"],
    "Subscription Renewal": ["Subscriptions", "Payments", "Invoicing", "Email"],
    "Product Onboarding": ["Catalog", "Media", "Search"],
    "Order Fulfilment": ["Orders", "Warehouse", "Shipping", "Notifications"],
    "Month-End Close": ["Ledger", "Reporting", "Exports"],
    "Account Signup": ["Identity", "Sessions", "Email"],
}
MISSING_SERVICES = ["Gift Card", "Telemetry", "Translation", "Marketplace", "Chat", "Insurance"]
MISSING_WORKFLOWS = ["Chargeback Dispute", "Supplier Onboarding", "Gift Wrapping"]


def slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def build(rng: random.Random):
    svc = {}
    for i, (name, purpose) in enumerate(SERVICES):
        # dependencies only on services listed earlier -> a DAG with realistic layering
        k = 0 if i < 4 else rng.choice([2, 2, 3])
        deps = rng.sample([n for n, _ in SERVICES[:i]], min(k, i))
        svc[name] = {
            "name": name, "file": f"{slug(name)}-service.md", "title": f"{name} Service", "purpose": purpose,
            "ident": f"{slug(name)}-svc", "deps": deps, "team": rng.choice(TEAMS), "oncall": rng.choice(PEOPLE),
            "port": rng.randrange(8000, 9900), "replicas": rng.choice([2, 3, 4, 6, 8]), "region": rng.choice(REGIONS),
            "store": rng.choice(STORES), "db": f"{slug(name).replace('-', '_')}_{rng.choice(['db', 'store', 'main'])}",
            "workflows": [w for w, ss in WORKFLOWS.items() if name in ss],
        }
    return svc


def page(s: dict, rng: random.Random) -> str:
    fm = [f"title: {s['title']}"]
    if s["deps"]:
        fm.append("depends_on: [" + ", ".join(f"{slug(d)}-service" for d in s["deps"]) + "]")
    if s["workflows"]:
        fm.append("part_of: [" + ", ".join(f"{slug(w)}-workflow" for w in s["workflows"]) + "]")
    out = ["---", *fm, "---", f"# {s['title']}", "", f"`{s['ident']}` {s['purpose']}.", ""]
    if s["deps"]:
        out += ["## Dependencies", ""]
        for d in s["deps"]:
            out.append(f"- [[{d} Service]] — called {rng.choice(REASONS).format(v=VERBS[d])}.")
        out.append("")
    owner = rng.choice([
        "Owned by the {team} team. Primary on-call: {oncall}; page #{t}-oncall for escalations.",
        "The {team} team owns this service. {oncall} is the primary on-call engineer (escalate in #{t}-oncall).",
        "Ownership: {team} team. On-call: {oncall}. Escalation channel: #{t}-oncall.",
    ]).format(team=s["team"], oncall=s["oncall"], t=s["team"].lower())
    runtime = rng.choice([
        "Listens on port {port}, {replicas} replicas in {region}. Primary datastore: {store} `{db}`.",
        "Runs {replicas} replicas in {region} and serves traffic on port {port}. Data lives in {store} `{db}`.",
        "Port {port}. Deployment: {replicas} replicas, region {region}. Storage: {store} `{db}`.",
    ]).format(**s)
    out += ["## Ownership", "", owner, "", "## Runtime", "", runtime, ""]
    if s["deps"]:
        d = s["deps"][0]
        out += ["## Failure behaviour", "",
                f"If the {d} Service is unavailable, {s['ident']} "
                + rng.choice(["retries with exponential backoff for up to 5 minutes",
                              "queues requests and replays them when it recovers",
                              "returns HTTP 503 and the caller retries", "fails open and logs a warning"]) + ".", ""]
    return "\n".join(out)


def workflow_page(name: str, steps: list[str]) -> str:
    out = ["---", f"title: {name} Workflow", "related: [" + ", ".join(f"{slug(s)}-service" for s in steps) + "]",
           "---", f"# {name} Workflow", "", "## Steps", ""]
    for i, s in enumerate(steps, 1):
        out.append(f"{i}. The [[{s} Service]] is used to {VERBS[s]}.")
    return "\n".join(out) + "\n"


ATTRS = {  # attribute -> (section anchor, phrasings for one service, phrasings for a set of services)
    "owner": ("ownership", ["Who owns the {x}?", "Which team is responsible for {x}?", "Who is on call for {x}?"],
              ["Which teams own {xs}?", "Who is on call for {xs}?"]),
    "port": ("runtime", ["Which port does {x} listen on?", "What port is {x} served on?"],
             ["Which ports do {xs} listen on?"]),
    "store": ("runtime", ["Which database does the {x} use?", "Where does {x} store its data?"],
              ["Which datastores do {xs} use?", "What databases are behind {xs}?"]),
}


def questions(svc: dict, rng: random.Random) -> list[dict]:
    qs = []

    def add(cls, query, evidence):
        qs.append({"id": f"g{len(qs) + 1:03d}", "class": cls, "query": query, "evidence": sorted(set(evidence))})

    def ref(name, anchor):
        return f"{svc[name]['file']}#{anchor}"

    def name_of(s):  # how a question refers to a service: page title or code identifier
        return rng.choice([s["title"], s["title"], f"`{s['ident']}`"]).replace("`", "")

    names = list(svc)
    for name in rng.sample(names, 24):  # single-hop
        attr = rng.choice(list(ATTRS))
        anchor, one, _ = ATTRS[attr]
        add("single", rng.choice(one).format(x=name_of(svc[name])), [ref(name, anchor)])
    with_deps = [n for n in names if len(svc[n]["deps"]) >= 2]
    for name in rng.sample(with_deps, 24):  # dependencies (2 hops)
        attr = rng.choice(list(ATTRS))
        anchor, _, many = ATTRS[attr]
        xs = rng.choice(["the services that {x} depends on", "the dependencies of {x}",
                         "the services called by {x}"]).format(x=name_of(svc[name]))
        add("deps", rng.choice(many).format(xs=xs),
            [ref(d, anchor) for d in svc[name]["deps"]] + [ref(name, "dependencies")])
    for wf, steps in WORKFLOWS.items():  # workflow (2 hops)
        attr = rng.choice(["owner", "store"])
        anchor, _, many = ATTRS[attr]
        xs = rng.choice(["the services involved in the {w} workflow", "every service in the {w} workflow"]).format(w=wf)
        add("workflow", rng.choice(many).format(xs=xs),
            [ref(s, anchor) for s in steps] + [f"{slug(wf)}-workflow.md#steps"])
    dependents = {n: [m for m in names if n in svc[m]["deps"]] for n in names}
    for name in rng.sample([n for n in names if 2 <= len(dependents[n]) <= 4], 10):  # impact (incoming links)
        q = rng.choice(["Which teams must be told before the {x} API changes?",
                        "Who owns the services that depend on {x}?",
                        "If {x} has an outage, which teams own the affected services?"]).format(x=name_of(svc[name]))
        add("impact", q, [ref(m, "ownership") for m in dependents[name]]
            + [ref(m, "dependencies") for m in dependents[name]])
    for i, m in enumerate(MISSING_SERVICES):  # unanswerable: same shapes, entity does not exist
        anchor, one, many = ATTRS[rng.choice(list(ATTRS))]
        x = f"{m} Service" if i % 2 else f"{slug(m)}-svc"
        add("none", rng.choice(one).format(x=x), [])
        add("none", rng.choice(many).format(xs=f"the services that {x} depends on"), [])
    for w in MISSING_WORKFLOWS:
        add("none", ATTRS["owner"][2][0].format(xs=f"the services involved in the {w} workflow"), [])
    for i, q in enumerate(qs):
        q["split"] = "val" if i % 2 == 0 else "test"
    return qs


def main(out: Path) -> None:
    rng = random.Random(SEED)
    svc = build(rng)
    wiki = out / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    for s in svc.values():
        (wiki / s["file"]).write_text(page(s, rng), encoding="utf-8")
    for wf, steps in WORKFLOWS.items():
        (wiki / f"{slug(wf)}-workflow.md").write_text(workflow_page(wf, steps), encoding="utf-8")
    qs = questions(svc, rng)
    (out / "queries.jsonl").write_text("".join(json.dumps(q) + "\n" for q in qs), encoding="utf-8")
    counts = {}
    for q in qs:
        counts[q["class"]] = counts.get(q["class"], 0) + 1
    print(f"{len(svc) + len(WORKFLOWS)} pages, {len(qs)} questions {counts} -> {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/graph-wiki"))
