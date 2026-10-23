// PDF viewer for /api/documents/{id}/view: pages drawn by PDF.js (vendored, works offline) with a selectable
// text layer and working links. Opens at #page=N. Pages are drawn as they scroll into view; the text of every
// page is laid out in the background so the browser's find (Ctrl+F) reaches the whole document.
import * as pdfjs from "./vendor/pdfjs/pdf.min.mjs";

pdfjs.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.min.mjs";

const main = document.getElementById("doc");
const status = document.getElementById("status");
const counter = document.getElementById("pages");
const pages = [];  // {el, page, viewport, drawn, text}
let pdf, scale = 0, fit = true;

const fail = (msg) => { status.hidden = false; status.textContent = msg; };

function targetPage() {
  const m = /(?:^#|&)page=(\d+)/.exec(location.hash);
  return m ? Math.min(Math.max(1, +m[1]), pdf.numPages) : 1;
}

function fitScale(page) {
  const w = page.getViewport({ scale: 1 }).width;
  return Math.min(Math.max((document.documentElement.clientWidth - 32) / w, 0.5), 2.5);
}

function layout() {
  for (const p of pages) {
    p.viewport = p.page.getViewport({ scale });
    p.el.style.width = `${p.viewport.width}px`;
    p.el.style.height = `${p.viewport.height}px`;
    p.el.style.setProperty("--scale-factor", scale);
    p.el.replaceChildren();
    p.drawn = p.text = false;
  }
  for (const p of pages) observer.unobserve(p.el), observer.observe(p.el);
}

async function draw(p) {
  if (p.drawn) return;
  p.drawn = true;
  const ratio = window.devicePixelRatio || 1;
  const canvas = document.createElement("canvas");
  canvas.width = Math.floor(p.viewport.width * ratio);
  canvas.height = Math.floor(p.viewport.height * ratio);
  p.el.prepend(canvas);
  try {
    await p.page.render({ canvasContext: canvas.getContext("2d"), viewport: p.viewport,
                          transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null }).promise;
  } catch (e) {
    console.error(e);
  }
  await text(p);
  await links(p);
}

async function text(p) {
  if (p.text) return;
  p.text = true;
  const div = document.createElement("div");
  div.className = "textLayer";
  p.el.append(div);
  await new pdfjs.TextLayer({ textContentSource: p.page.streamTextContent(), container: div,
                              viewport: p.viewport }).render();
}

async function links(p) {
  for (const a of await p.page.getAnnotations()) {
    if (a.subtype !== "Link" || !(a.url || a.dest)) continue;
    const [x1, y1, x2, y2] = p.viewport.convertToViewportRectangle(a.rect);
    const link = document.createElement("a");
    link.className = "link";
    Object.assign(link.style, { left: `${Math.min(x1, x2)}px`, top: `${Math.min(y1, y2)}px`,
                                width: `${Math.abs(x2 - x1)}px`, height: `${Math.abs(y2 - y1)}px` });
    if (a.url) {
      link.href = a.url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.title = a.url;
    } else {
      link.href = "#";
      link.addEventListener("click", async (ev) => {
        ev.preventDefault();
        const dest = typeof a.dest === "string" ? await pdf.getDestination(a.dest) : a.dest;
        if (dest) go(1 + (typeof dest[0] === "object" ? await pdf.getPageIndex(dest[0]) : dest[0]));
      });
    }
    p.el.append(link);
  }
}

function go(n) {
  history.replaceState(null, "", `#page=${n}`);
  pages[n - 1].el.scrollIntoView();
}

const observer = new IntersectionObserver((entries) => {
  for (const e of entries) if (e.isIntersecting) draw(pages[+e.target.dataset.n - 1]);
}, { rootMargin: "800px 0px" });

const seen = new IntersectionObserver((entries) => {
  const top = entries.filter((e) => e.isIntersecting).map((e) => +e.target.dataset.n).sort((a, b) => a - b)[0];
  if (top) counter.textContent = `Page ${top} of ${pdf.numPages}`;
}, { threshold: 0.5 });

async function textInBackground() {
  for (const p of pages) {
    await new Promise((r) => (window.requestIdleCallback || setTimeout)(r));
    if (!p.text) await text(p).catch(() => {});
  }
}

function zoom(next) {
  const at = visiblePage();
  scale = next;
  layout();
  pages[at - 1].el.scrollIntoView();
  textInBackground();
}

function visiblePage() {
  const mid = innerHeight / 3;
  return +(pages.find((p) => p.el.getBoundingClientRect().bottom > mid) || pages[0]).el.dataset.n;
}

document.getElementById("zoom-in").onclick = () => { fit = false; zoom(Math.min(scale * 1.25, 5)); };
document.getElementById("zoom-out").onclick = () => { fit = false; zoom(Math.max(scale / 1.25, 0.25)); };
document.getElementById("zoom-fit").onclick = () => { fit = true; zoom(fitScale(pages[0].page)); };
addEventListener("hashchange", () => go(targetPage()));
let resizing;
addEventListener("resize", () => {
  clearTimeout(resizing);
  resizing = setTimeout(() => { if (fit && pages.length) zoom(fitScale(pages[0].page)); }, 200);
});

try {
  pdf = await pdfjs.getDocument({
    url: main.dataset.src, isEvalSupported: false, withCredentials: true,
    cMapUrl: "/vendor/pdfjs/cmaps/", cMapPacked: true, standardFontDataUrl: "/vendor/pdfjs/standard_fonts/",
  }).promise;
  for (let n = 1; n <= pdf.numPages; n++) {
    const el = document.createElement("div");
    el.className = "page";
    el.dataset.n = n;
    el.setAttribute("aria-label", `Page ${n}`);
    main.append(el);
    pages.push({ el, page: await pdf.getPage(n) });
  }
  status.hidden = true;
  scale = fitScale(pages[0].page);
  layout();
  pages.forEach((p) => seen.observe(p.el));
  const start = targetPage();
  pages[start - 1].el.scrollIntoView();
  counter.textContent = `Page ${start} of ${pdf.numPages}`;
  textInBackground();
} catch (e) {
  console.error(e);
  fail(e?.name === "PasswordException" ? "This PDF is password-protected: download it to open it."
                                         : "This PDF could not be shown here. Use the browser's viewer or download it.");
}
