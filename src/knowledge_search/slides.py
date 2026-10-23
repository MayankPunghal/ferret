"""PowerPoint slides drawn as HTML for the document viewer, from the .pptx itself (no Office, no LibreOffice).

Each slide is a box with the slide's aspect ratio; every shape is placed at its position in percent of the slide,
and lengths and font sizes are in `cqw` (percent of the slide's width), so a slide scales as one picture. Drawn:
text with its size, weight, colour, alignment and bullets; fills (solid, gradient, picture), outlines and the
common preset shapes (rectangles, rounded, ellipses, arrows, chevrons, …) and custom outlines; pictures (with
cropping); lines and connectors (straight and elbow); tables; groups; SmartArt as PowerPoint last drew it
(organisation charts included); the layout's and master's own shapes (logos, bars) and backgrounds; theme colours
and fonts. Not drawn: charts, video, 3-D, shadows and other effects; EMF/WMF pictures show as a labelled box.
Everything is escaped text, generated CSS and data: URIs, so nothing in a deck can run.
"""

import base64
import colorsys
import html
import io
import posixpath
import re
import zipfile
from xml.etree import ElementTree as ET

from .convert import (
    DSP,
    A,
    P,
    R,
    _xml,
    pptx_to_markdown,
    rels,
    slide_parts,
    smartart_lines,
    smartart_parts,
)
from .parsing import slugify

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".bmp": "image/bmp", ".webp": "image/webp", ".svg": "image/svg+xml", ".tif": "image/tiff",
               ".tiff": "image/tiff"}
MAX_IMAGES = 80_000_000  # total picture bytes inlined per deck; further pictures show as boxes
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
TABLE = "{http://schemas.openxmlformats.org/drawingml/2006/table}"  # graphicData uri, for reference only
PRESET_COLORS = {"black": "000000", "white": "FFFFFF", "red": "FF0000", "green": "008000", "blue": "0000FF",
                 "yellow": "FFFF00", "gray": "808080", "grey": "808080", "darkGray": "A9A9A9", "lightGray": "D3D3D3"}


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class Deck:
    def __init__(self, z: zipfile.ZipFile):
        self.z = z
        self.names = set(z.namelist())
        sz = _xml(z, "ppt/presentation.xml").find(f"{P}sldSz")
        self.w = _int(sz.get("cx"), 12192000) if sz is not None else 12192000
        self.h = _int(sz.get("cy"), 6858000) if sz is not None else 6858000
        self.default_style = _xml(z, "ppt/presentation.xml").find(f"{P}defaultTextStyle")
        self._rels, self._xmls, self.image_bytes = {}, {}, 0

    # ------------------------------------------------------------------ package
    def xml(self, part):
        if part not in self._xmls:
            self._xmls[part] = _xml(self.z, part) if part in self.names else None
        return self._xmls[part]

    def rels(self, part):
        if part not in self._rels:
            self._rels[part] = rels(self.z, part)
        return self._rels[part]

    def related(self, part, kind):
        return next((t for k, t in self.rels(part).values() if k == kind and t in self.names), None)

    def image(self, part, rid) -> str | None:
        target = self.rels(part).get(rid, (None, None))[1]
        mime = IMAGE_TYPES.get(posixpath.splitext(target or "")[1].lower())
        if not target or target not in self.names or not mime:
            return None
        size = self.z.getinfo(target).file_size
        if self.image_bytes + size > MAX_IMAGES:
            return None
        self.image_bytes += size
        return f"data:{mime};base64,{base64.b64encode(self.z.read(target)).decode()}"

    # ------------------------------------------------------------------ units
    def x(self, emu):  # horizontal position or size, % of the slide width
        return f"{emu * 100 / self.w:.3f}%"

    def y(self, emu):
        return f"{emu * 100 / self.h:.3f}%"

    def u(self, emu):  # any length, in % of the slide width (cqw)
        return f"{emu * 100 / self.w:.4f}cqw"

    def pt(self, points):
        return self.u(points * 12700)


class Slide:
    """One slide with its layout, master and theme: resolves inherited placeholders, text styles and colours."""

    def __init__(self, deck: Deck, part: str):
        self.d, self.part = deck, part
        self.sld = deck.xml(part)
        self.layout_part = deck.related(part, "slideLayout")
        self.layout = deck.xml(self.layout_part) if self.layout_part else None
        self.master_part = deck.related(self.layout_part, "slideMaster") if self.layout_part else None
        self.master = deck.xml(self.master_part) if self.master_part else None
        theme_part = deck.related(self.master_part, "theme") if self.master_part else None
        theme = deck.xml(theme_part) if theme_part else None
        self.colors, self.fonts = {}, {"major": "", "minor": ""}
        self.fill_styles, self.bg_styles = [], []
        if theme is not None:
            fmt = theme.find(f".//{A}fmtScheme")
            if fmt is not None:
                self.fill_styles = list(fmt.find(f"{A}fillStyleLst") if fmt.find(f"{A}fillStyleLst") is not None else [])
                self.bg_styles = list(fmt.find(f"{A}bgFillStyleLst") if fmt.find(f"{A}bgFillStyleLst") is not None else [])
            for c in theme.find(f".//{A}clrScheme") if theme.find(f".//{A}clrScheme") is not None else []:
                v = c[0] if len(c) else None
                if v is not None:
                    self.colors[_tag(c)] = v.get("val") if _tag(v) == "srgbClr" else v.get("lastClr", "000000")
            for k in ("major", "minor"):
                f = theme.find(f".//{A}{k}Font/{A}latin")
                self.fonts[k] = f.get("typeface", "") if f is not None else ""
        cmap = self.master.find(f"{P}clrMap") if self.master is not None else None
        self.clr_map = dict(cmap.attrib) if cmap is not None else {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
        ovr = self.sld.find(f"{P}clrMapOvr/{A}overrideClrMapping")
        if ovr is not None:
            self.clr_map = dict(ovr.attrib)

    # ------------------------------------------------------------------ colours and fills
    def color(self, parent, ph_color=None) -> str | None:
        """CSS colour of the first colour element in `parent` (srgb, scheme, system, preset), with its modifiers."""
        for c in parent if parent is not None else []:
            t, v = _tag(c), c.get("val", "")
            if t == "srgbClr":
                rgb = v
            elif t == "schemeClr":
                rgb = (ph_color or "").lstrip("#")[:6] if v == "phClr" else self.colors.get(self.clr_map.get(v, v), self.colors.get(v))
            elif t == "sysClr":
                rgb = c.get("lastClr") or ("000000" if v == "windowText" else "FFFFFF")
            elif t == "prstClr":
                rgb = PRESET_COLORS.get(v, "000000")
            elif t == "scrgbClr":
                rgb = "".join(f"{min(255, max(0, round(_int(c.get(k)) / 100000 * 255))):02X}" for k in "rgb")
            else:
                continue
            if not rgb or not re.fullmatch(r"[0-9A-Fa-f]{6}", rgb):
                return None
            return self._modify(rgb, c)
        return None

    @staticmethod
    def _modify(rgb, c) -> str:
        r, g, b = (int(rgb[i:i + 2], 16) / 255 for i in (0, 2, 4))
        alpha = 1.0
        for m in c:
            t, v = _tag(m), _int(m.get("val")) / 100000
            if t in ("lumMod", "lumOff", "satMod", "satOff", "hueOff", "hueMod"):
                h, l, s = colorsys.rgb_to_hls(r, g, b)
                l = l * v if t == "lumMod" else l + v if t == "lumOff" else l
                s = min(1, max(0, s * v if t == "satMod" else s + v if t == "satOff" else s))
                h = (h + _int(m.get("val")) / 60000 / 360) % 1 if t == "hueOff" else (h * v) % 1 if t == "hueMod" else h
                r, g, b = colorsys.hls_to_rgb(h, min(1, max(0, l)), s)
            elif t in ("tint", "shade"):  # applied to linear light, as PowerPoint does (75% tint of black is #888)
                lin = (x ** 2.2 for x in (r, g, b))
                r, g, b = ((1 - (1 - x) * v if t == "tint" else x * v) ** (1 / 2.2) for x in lin)
            elif t == "alpha":
                alpha = v
        r, g, b = (round(min(1, max(0, x)) * 255) for x in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}" if alpha >= 1 else f"rgba({r},{g},{b},{alpha:.2f})"

    def fill(self, sppr, style, part, ph=None, styles=None) -> str | None:
        """CSS background for a shape's fill; 'transparent' for no fill; None when nothing says (no fill).
        `ph`: the colour that theme styles call phClr; `styles`: the theme's fill list a style refers to."""
        for c in sppr if sppr is not None else []:
            t = _tag(c)
            if t == "noFill":
                return "transparent"
            if t == "solidFill":
                return self.color(c, ph)
            if t == "gradFill":
                stops = [(_int(s.get("pos")) / 1000, self.color(s, ph)) for s in c.iter(f"{A}gs")]
                stops = [f"{col} {pos:.0f}%" for pos, col in sorted(stops) if col]
                lin = c.find(f"{A}lin")
                angle = (_int(lin.get("ang")) / 60000 + 90) % 360 if lin is not None else 180
                if c.find(f"{A}path") is not None and len(stops) > 1:
                    return f"radial-gradient({', '.join(stops)})"
                return f"linear-gradient({angle:.0f}deg, {', '.join(stops)})" if len(stops) > 1 else (
                    stops[0].split()[0] if stops else None)
            if t == "blipFill":
                blip = c.find(f"{A}blip")
                uri = self.d.image(part, blip.get(f"{R}embed")) if blip is not None else None
                if not uri:
                    return None
                return f"url('{uri}') repeat" if c.find(f"{A}tile") is not None else f"center / 100% 100% no-repeat url('{uri}')"
        ref = style.find(f"{A}fillRef") if style is not None else None
        if ref is not None:
            return self.ref_fill(ref, self.fill_styles, part)
        return None

    def ref_fill(self, ref, styles, part) -> str | None:
        """A fill taken from the theme's list (fillRef idx 1–3, background idx 1001–1003), in the ref's colour."""
        idx = _int(ref.get("idx"))
        idx = idx - 1001 if idx > 1000 else idx - 1
        if idx < 0:
            return None
        ph = self.color(ref)
        if 0 <= idx < len(styles):
            return self.fill([styles[idx]], None, part, ph=ph[1:7] if ph and ph.startswith("#") else None) or ph
        return ph

    def line(self, sppr, style) -> tuple[str | None, float]:
        """(colour or None for no line, width in EMU)."""
        ln = sppr.find(f"{A}ln") if sppr is not None else None
        width = _int(ln.get("w"), 12700) if ln is not None else 12700
        if ln is not None:
            if ln.find(f"{A}noFill") is not None:
                return None, 0
            fill = ln.find(f"{A}solidFill")
            if fill is not None:
                return self.color(fill), width
            if ln.find(f"{A}gradFill") is not None:
                return self.color(ln.find(f"{A}gradFill/{A}gsLst/{A}gs")), width
        ref = style.find(f"{A}lnRef") if style is not None else None
        if ref is not None and _int(ref.get("idx")) > 0:
            return self.color(ref), width
        return None, 0

    # ------------------------------------------------------------------ placeholders and text styles
    def _ph_match(self, tree, ph):
        if tree is None or ph is None:
            return None
        typ, idx = ph.get("type", "body" if ph.get("idx") else "obj"), ph.get("idx")
        same = {"title": {"title", "ctrTitle"}, "ctrTitle": {"title", "ctrTitle"}}.get(typ, {typ})
        cands = [(sp, sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")) for sp in tree.iter(f"{P}sp")]
        cands = [(sp, p) for sp, p in cands if p is not None]
        for sp, p in cands:
            if idx is not None and p.get("idx") == idx:
                return sp
        for sp, p in cands:
            if p.get("type", "body" if p.get("idx") else "obj") in same:
                return sp
        if typ in ("obj", "body", "subTitle"):  # content placeholders inherit from the master's body
            return next((sp for sp, p in cands if p.get("type") == "body"), None)
        return None

    def inherited(self, sp):
        """[layout placeholder, master placeholder] that a slide placeholder inherits from (None if none)."""
        ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
        if ph is None:
            return []
        lay = self._ph_match(self.layout, ph)
        mas = self._ph_match(self.master, lay.find(f"{P}nvSpPr/{P}nvPr/{P}ph") if lay is not None else ph)
        return [x for x in (lay, mas) if x is not None]

    def text_styles(self, sp, kind):
        """List styles for a shape's text, in priority order: (its own and its layout and master placeholders',
        then the master's text style of its kind and the presentation default)."""
        own = [x.find(f"{P}txBody/{A}lstStyle") for x in [sp] + self.inherited(sp)]
        tx = self.master.find(f"{P}txStyles") if self.master is not None else None
        name = {"title": "titleStyle", "body": "bodyStyle"}.get(kind, "otherStyle")
        rest = [tx.find(f"{P}{name}") if tx is not None and kind != "other" else None, self.d.default_style]
        return [x for x in own if x is not None], [x for x in rest if x is not None]


def _kind(sp) -> str:
    ph = sp.find(f"{P}nvSpPr/{P}nvPr/{P}ph")
    if ph is None:
        return "other"
    t = ph.get("type", "body" if ph.get("idx") else "obj")
    return "title" if t in ("title", "ctrTitle") else "body" if t in ("body", "obj", "subTitle") else "other"


# Preset outlines in a 0–100 box (SVG path data); anything else is drawn as a rectangle.
SHAPES = {
    "triangle": "M50 0 L100 100 L0 100 Z", "rtTriangle": "M0 0 L100 100 L0 100 Z",
    "diamond": "M50 0 L100 50 L50 100 L0 50 Z", "parallelogram": "M25 0 L100 0 L75 100 L0 100 Z",
    "trapezoid": "M25 0 L75 0 L100 100 L0 100 Z", "pentagon": "M50 0 L100 38 L81 100 L19 100 L0 38 Z",
    "hexagon": "M25 0 L75 0 L100 50 L75 100 L25 100 L0 50 Z",
    "octagon": "M29 0 L71 0 L100 29 L100 71 L71 100 L29 100 L0 71 L0 29 Z",
    "homePlate": "M0 0 L80 0 L100 50 L80 100 L0 100 Z", "chevron": "M0 0 L80 0 L100 50 L80 100 L0 100 L20 50 Z",
    "rightArrow": "M0 25 L70 25 L70 0 L100 50 L70 100 L70 75 L0 75 Z",
    "leftArrow": "M100 25 L30 25 L30 0 L0 50 L30 100 L30 75 L100 75 Z",
    "upArrow": "M25 100 L25 30 L0 30 L50 0 L100 30 L75 30 L75 100 Z",
    "downArrow": "M25 0 L25 70 L0 70 L50 100 L100 70 L75 70 L75 0 Z",
    "leftRightArrow": "M0 50 L20 0 L20 25 L80 25 L80 0 L100 50 L80 100 L80 75 L20 75 L20 100 Z",
    "star5": "M50 0 L61 35 L98 35 L68 57 L79 91 L50 70 L21 91 L32 57 L2 35 L39 35 Z",
    "plus": "M33 0 L67 0 L67 33 L100 33 L100 67 L67 67 L67 100 L33 100 L33 67 L0 67 L0 33 L33 33 Z",
    "flowChartDecision": "M50 0 L100 50 L50 100 L0 50 Z",
    "flowChartTerminator": "M16 0 L84 0 A16 50 0 0 1 84 100 L16 100 A16 50 0 0 1 16 0 Z",
    "wedgeRectCallout": "M0 0 L100 0 L100 80 L40 80 L20 100 L25 80 L0 80 Z",
}
SYMBOL_BULLETS = {"§": "▪", "Ø": "➢", "ü": "✓", "q": "❑", "v": "❖", "n": "■", "l": "●", "Ÿ": "•", "·": "•",
                  "o": "○", "": "•", "è": "➔", "à": "➔", "Ä": "►", "w": "◆", "u": "◆"}
ROUND = {"roundRect": "16.7%", "ellipse": "50%", "flowChartConnector": "50%", "round2SameRect": "16.7% 16.7% 0 0",
         "snipRoundRect": "16.7% 0 0 0", "flowChartAlternateProcess": "16.7%"}
LINES = {"line", "straightConnector1"}
ELBOWS = {"bentConnector2", "bentConnector3", "bentConnector4", "bentConnector5"}


def _preset(sppr) -> str:
    geom = sppr.find(f"{A}prstGeom") if sppr is not None else None
    if geom is not None:
        return geom.get("prst", "rect")
    return "custom" if sppr is not None and sppr.find(f"{A}custGeom") is not None else "rect"


def _adjust(sppr, name, default):
    gd = sppr.find(f"{A}prstGeom/{A}avLst/{A}gd[@name='{name}']") if sppr is not None else None
    m = re.match(r"val\s+(-?\d+)", gd.get("fmla", "")) if gd is not None else None
    return int(m.group(1)) if m else default


def _outline(prst, sppr) -> str | None:
    """SVG path data of a shape's outline in a 0–100 box; None for shapes drawn with CSS (rectangles, rounded)."""
    if prst in SHAPES:
        return SHAPES[prst]
    if prst == "custom":
        return Renderer.custom_path(sppr.find(f"{A}custGeom"))
    if prst in ("pie", "arc", "chord", "blockArc"):  # angles in 60000ths of a degree, clockwise from 3 o'clock
        import math
        a1, a2 = _adjust(sppr, "adj1", 0) / 60000, _adjust(sppr, "adj2", 16200000) / 60000
        sweep = (a2 - a1) % 360 or 360

        def pt(a, r=50):
            return f"{50 + r * math.cos(math.radians(a)):.2f} {50 + r * math.sin(math.radians(a)):.2f}"

        large = 1 if sweep > 180 else 0
        if prst == "blockArc":
            inner = 50 - _adjust(sppr, "adj3", 25000) / 1000
            return (f"M{pt(a1)} A50 50 0 {large} 1 {pt(a1 + sweep)} L{pt(a1 + sweep, inner)} "
                    f"A{inner:.2f} {inner:.2f} 0 {large} 0 {pt(a1, inner)} Z")
        return f"M50 50 L{pt(a1)} A50 50 0 {large} 1 {pt(a1 + sweep)} Z" if prst == "pie" else (
            f"M{pt(a1)} A50 50 0 {large} 1 {pt(a1 + sweep)}" + (" Z" if prst == "chord" else ""))
    if prst in ("wedgeRectCallout", "wedgeRoundRectCallout", "wedgeEllipseCallout"):
        tx, ty = 50 + _adjust(sppr, "adj1", -20833) / 1000, 50 + _adjust(sppr, "adj2", 62500) / 1000
        if ty > 100:
            return f"M0 0 H100 V100 H{max(tx, 40) if tx > 50 else 37} L{tx:.1f} {ty:.1f} L{20 if tx < 50 else 60} 100 H0 Z"
        if ty < 0:
            return f"M0 0 H{20 if tx < 50 else 60} L{tx:.1f} {ty:.1f} L{37 if tx < 50 else 80} 0 H100 V100 H0 Z"
        if tx < 0:
            return f"M0 0 H100 V100 H0 V{60} L{tx:.1f} {ty:.1f} L0 {40} Z"
        return f"M0 0 H100 V{40} L{tx:.1f} {ty:.1f} L100 {60} V100 H0 Z"
    return None


def _polygon(path: str) -> str | None:
    """CSS polygon() of an outline made only of straight lines (so any fill can be clipped to it)."""
    if re.search(r"[^MLZHV\d.\s-]", path) or path.count("M") != 1:
        return None
    pts, x, y = [], 0.0, 0.0
    for cmd, args in re.findall(r"([MLHV])\s*([-\d.\s]+)", path):
        n = [float(v) for v in args.split()]
        if cmd in "ML":
            x, y = n[0], n[1]
        elif cmd == "H":
            x = n[0]
        else:
            y = n[0]
        pts.append(f"{x:.2f}% {y:.2f}%")
    return ", ".join(pts) if len(pts) >= 3 else None


DEFAULT_TABLE_STYLE = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
# PowerPoint's default table style, which files using it often do not include: white grid, accent 1 header,
# rows tinted 20% / 40% of accent 1.
MEDIUM_STYLE_2 = f"""<a:tblStyle xmlns:a="{A[1:-1]}" styleId="{DEFAULT_TABLE_STYLE}">
<a:wholeTbl><a:tcTxStyle><a:schemeClr val="dk1"/></a:tcTxStyle><a:tcStyle><a:tcBdr><a:insideH><a:ln w="12700">
<a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:insideH></a:tcBdr><a:fill><a:solidFill>
<a:schemeClr val="accent1"><a:tint val="20000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle></a:wholeTbl>
<a:band1H><a:tcStyle><a:fill><a:solidFill><a:schemeClr val="accent1"><a:tint val="40000"/></a:schemeClr>
</a:solidFill></a:fill></a:tcStyle></a:band1H>
<a:firstCol><a:tcTxStyle b="on"><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:fill><a:solidFill>
<a:schemeClr val="accent1"/></a:solidFill></a:fill></a:tcStyle></a:firstCol>
<a:lastRow><a:tcTxStyle b="on"><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:fill><a:solidFill>
<a:schemeClr val="accent1"/></a:solidFill></a:fill></a:tcStyle></a:lastRow>
<a:firstRow><a:tcTxStyle b="on"><a:schemeClr val="lt1"/></a:tcTxStyle><a:tcStyle><a:fill><a:solidFill>
<a:schemeClr val="accent1"/></a:solidFill></a:fill></a:tcStyle></a:firstRow></a:tblStyle>"""


class Renderer:
    def __init__(self, data: bytes):
        self.data = data
        self.z = zipfile.ZipFile(io.BytesIO(data))
        self.d = Deck(self.z)
        self.ids = 0  # arrow markers need ids unique in the page

    def slides(self) -> list[dict]:
        headings = [ln[3:] for ln in pptx_to_markdown(self.data)[1].splitlines() if ln.startswith("## ")]
        out = []
        for n, part in enumerate(slide_parts(self.z), 1):
            s = Slide(self.d, part)
            notes_part = self.d.related(part, "notesSlide")
            notes = ""
            if notes_part:
                from .convert import paragraphs
                notes = " ".join(t for t in paragraphs(self.d.xml(notes_part)) if not t.isdigit())
            out.append({"n": n, "html": self.slide(s), "notes": notes, "hidden": s.sld.get("show") == "0",
                        "anchor": slugify(headings[n - 1]) if n <= len(headings) else f"slide-{n}"})
        return out

    # ------------------------------------------------------------------ slide
    def slide(self, s: Slide) -> str:
        bg = self.background(s)
        parts = []
        show_master = s.sld.get("showMasterSp") != "0"
        if show_master and s.master is not None and (s.layout is None or s.layout.get("showMasterSp") != "0"):
            parts += self.tree(s, s.master.find(f"{P}cSld/{P}spTree"), s.master_part, decor=True)
        if show_master and s.layout is not None:
            parts += self.tree(s, s.layout.find(f"{P}cSld/{P}spTree"), s.layout_part, decor=True)
        parts += self.tree(s, s.sld.find(f"{P}cSld/{P}spTree"), s.part)
        return f'<div class="slide" style="aspect-ratio:{self.d.w}/{self.d.h};background:{bg}">{"".join(parts)}</div>'

    def background(self, s: Slide) -> str:
        for tree, part in ((s.sld, s.part), (s.layout, s.layout_part), (s.master, s.master_part)):
            bg = tree.find(f"{P}cSld/{P}bg") if tree is not None else None
            if bg is None:
                continue
            pr, ref = bg.find(f"{P}bgPr"), bg.find(f"{P}bgRef")
            if pr is not None:
                return s.fill(pr, None, part) or "#fff"
            if ref is not None:
                return s.ref_fill(ref, s.bg_styles if _int(ref.get("idx")) > 1000 else s.fill_styles, part) or "#fff"
        return "#fff"

    def tree(self, s, tree, part, decor=False, xf=None) -> list[str]:
        """Shapes of a shape tree; `xf` maps child coordinates of a group to slide coordinates."""
        out = []
        xf = xf or (lambda x, y, w, h: (x, y, w, h))
        for el in tree if tree is not None else []:
            t = _tag(el)
            try:
                if t == "grpSp":
                    out += self.tree(s, el, part, decor, self.group(el, xf))
                elif t in ("sp", "cxnSp", "pic", "graphicFrame"):
                    if decor and el.find(f".//{P}nvPr/{P}ph") is not None:
                        continue  # a layout's or master's placeholders show only through slides that use them
                    out.append(self.shape(s, el, part, xf))
            except (ValueError, KeyError, zipfile.BadZipFile) as e:  # one odd shape must not lose the slide
                out.append(f'<div class="s missing" style="left:0;top:0">{html.escape(type(e).__name__)}</div>')
        return out

    @staticmethod
    def group(el, xf):
        x = el.find(f"{P}grpSpPr/{A}xfrm")
        if x is None or x.find(f"{A}off") is None or x.find(f"{A}chOff") is None:
            return xf
        off, ext, choff, chext = (x.find(f"{A}{k}") for k in ("off", "ext", "chOff", "chExt"))
        sx = _int(ext.get("cx")) / (_int(chext.get("cx")) or 1) if chext is not None and ext is not None else 1
        sy = _int(ext.get("cy")) / (_int(chext.get("cy")) or 1) if chext is not None and ext is not None else 1
        ox, oy, cx0, cy0 = _int(off.get("x")), _int(off.get("y")), _int(choff.get("x")), _int(choff.get("y"))
        return lambda a, b, w, h: xf(ox + (a - cx0) * sx, oy + (b - cy0) * sy, w * sx, h * sy)

    def box(self, s, el, sppr):
        """(x, y, w, h, rotation degrees, flipH, flipV) in slide EMU, inheriting a placeholder's position."""
        xfrm = (el.find(f"{P}xfrm") if _tag(el) == "graphicFrame" else
                sppr.find(f"{A}xfrm") if sppr is not None else None)
        if (xfrm is None or xfrm.find(f"{A}off") is None) and _tag(el) == "sp":
            for inh in s.inherited(el):
                xfrm = inh.find(f"{P}spPr/{A}xfrm")
                if xfrm is not None and xfrm.find(f"{A}off") is not None:
                    break
        if xfrm is None or xfrm.find(f"{A}off") is None:
            if _tag(el) != "sp" or not "".join(el.itertext()).strip():
                return None
            # text with no position anywhere (no layout, as some generators write): PowerPoint's default areas
            w, h = self.d.w, self.d.h
            top, height = (0.05, 0.17) if _kind(el) == "title" else (0.25, 0.65)
            return (int(w * 0.05), int(h * top), int(w * 0.9), int(h * height), 0, False, False)
        off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
        return (_int(off.get("x")), _int(off.get("y")), _int(ext.get("cx")) if ext is not None else 0,
                _int(ext.get("cy")) if ext is not None else 0, _int(xfrm.get("rot")) / 60000,
                xfrm.get("flipH") == "1", xfrm.get("flipV") == "1")

    def place(self, xf, b, extra="", cls="s"):
        x, y, w, h = xf(*b[:4])
        rot = f"transform:rotate({b[4]:.2f}deg);" if b[4] else ""
        return (f'<div class="{cls}" style="left:{self.d.x(x)};top:{self.d.y(y)};width:{self.d.x(w)};'
                f'height:{self.d.y(h)};{rot}{extra}">'), (w, h)

    # ------------------------------------------------------------------ shapes
    def shape(self, s, el, part, xf) -> str:
        t = _tag(el)
        if t == "graphicFrame":
            return self.frame(s, el, part, xf)
        sppr = el.find(f"{P}spPr")
        b = self.box(s, el, sppr)
        if b is None:
            return ""
        style = el.find(f"{P}style")
        if t == "pic":
            return self.picture(s, el, part, xf, b)
        prst = _preset(sppr)
        if t == "cxnSp" or prst in LINES or prst in ELBOWS:
            return self.connector(s, sppr, style, xf, b, prst)
        fill, (color, width) = s.fill(sppr, style, part), s.line(sppr, style)
        for inh in s.inherited(el) if t == "sp" else []:  # a placeholder without its own fill or line
            isp, ist = inh.find(f"{P}spPr"), inh.find(f"{P}style")
            fill = fill or s.fill(isp, ist, s.layout_part)
            color, width = (color, width) if color else s.line(isp, ist)
        return self.paint(xf, b, prst, sppr, fill, color, width) + self.text(s, el, el.find(f"{P}txBody"), style) + "</div>"

    def paint(self, xf, b, prst, sppr, fill, color, width) -> str:
        """The opening tag of a shape's box with its fill and outline: CSS for rectangles, rounded rectangles
        and ellipses; other outlines clip the fill (any fill: colour, gradient, picture) and are stroked in SVG."""
        path = _outline(prst, sppr)
        paint_fill = fill and fill != "transparent"
        if path is None:
            css = (f"background:{fill};" if paint_fill else "") + (
                f"border:max(1px,{self.d.u(width)}) solid {color};" if color else "") + (
                f"border-radius:{ROUND[prst]};" if prst in ROUND else "")
            return self.place(xf, b, css)[0]
        flip = "".join(t for f, t in ((b[5], " scale(-1,1)"), (b[6], " scale(1,-1)")) if f)
        inner = ""
        poly = _polygon(path)
        if paint_fill and poly:
            inner += (f'<div class="fill" style="background:{fill};clip-path:polygon({poly});'
                      + (f"transform:{flip.strip()};" if flip else "") + '"></div>')
        solid = None if poly or not paint_fill else (re.search(r"#[0-9a-f]{6}|rgba\([^)]*\)", fill) or [None])[0]
        svg_flip = (f' transform="translate({100 if b[5] else 0} {100 if b[6] else 0})'
                    f' scale({-1 if b[5] else 1} {-1 if b[6] else 1})"') if flip else ""
        if color or solid:
            inner += (f'<svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d="{path}"{svg_flip} '
                      f'fill="{solid or "none"}" stroke="{color or "none"}" stroke-width="{self._stroke(width, 0, 0)}" '
                      f'vector-effect="non-scaling-stroke"/></svg>')
        return self.place(xf, b)[0] + inner

    @staticmethod
    def _stroke(width, w, h):
        return f"{max(1, width / 12700 * 1.33):.1f}px"

    @staticmethod
    def custom_path(cust) -> str:
        if cust is None:
            return "M0 0 H100 V100 H0 Z"
        out = []
        for p in cust.iter(f"{A}path"):
            pw, ph = _int(p.get("w"), 0), _int(p.get("h"), 0)
            pts = [(_int(q.get("x")), _int(q.get("y"))) for q in p.iter(f"{A}pt")]
            pw = pw or max((x for x, _ in pts), default=1) or 1
            ph = ph or max((y for _, y in pts), default=1) or 1

            def xy(q, pw=pw, ph=ph):
                return f"{_int(q.get('x')) * 100 / pw:.2f} {_int(q.get('y')) * 100 / ph:.2f}"

            for c in p:
                t = _tag(c)
                q = list(c.iter(f"{A}pt"))
                if t == "moveTo" and q:
                    out.append("M" + xy(q[0]))
                elif t == "lnTo" and q:
                    out.append("L" + xy(q[0]))
                elif t == "cubicBezTo" and len(q) == 3:
                    out.append("C" + " ".join(xy(x) for x in q))
                elif t == "quadBezTo" and len(q) == 2:
                    out.append("Q" + " ".join(xy(x) for x in q))
                elif t == "close":
                    out.append("Z")
        return " ".join(out) or "M0 0 H100 V100 H0 Z"

    def connector(self, s, sppr, style, xf, b, prst) -> str:
        """A line or connector, drawn in slide coordinates (a box of zero width or height still shows its line)."""
        color, width = s.line(sppr, style)
        if not color:
            color, width = s.color(style.find(f"{A}lnRef") if style is not None else None) or "#7f7f7f", width or 12700
        x, y, w, h = xf(*b[:4])
        x0, x1 = (x + w, x) if b[5] else (x, x + w)
        y0, y1 = (y + h, y) if b[6] else (y, y + h)
        d = (f"M{x0:.0f} {y0:.0f} H{(x0 + x1) / 2:.0f} V{y1:.0f} H{x1:.0f}" if prst in ELBOWS
             else f"M{x0:.0f} {y0:.0f} L{x1:.0f} {y1:.0f}")
        rot = f' transform="rotate({b[4]:.2f} {x + w / 2:.0f} {y + h / 2:.0f})"' if b[4] else ""
        ln = sppr.find(f"{A}ln") if sppr is not None else None
        dash = ' stroke-dasharray="4 3"' if ln is not None and ln.find(f"{A}prstDash") is not None and \
            ln.find(f"{A}prstDash").get("val", "solid") != "solid" else ""

        def has(end):
            e = ln.find(f"{A}{end}") if ln is not None else None
            return e is not None and e.get("type", "none") != "none"

        head, tail = has("tailEnd"), has("headEnd")
        self.ids += 1
        mid = f"arrow{self.ids}"
        arrow = (f'<marker id="{mid}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" '
                 f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="{color}"/></marker>') if head or tail else ""
        ends = (f' marker-end="url(#{mid})"' if head else "") + (f' marker-start="url(#{mid})"' if tail else "")
        return (f'<svg class="line" viewBox="0 0 {self.d.w} {self.d.h}" preserveAspectRatio="none">{arrow}'
                f'<path d="{d}"{rot} fill="none" stroke="{color}" stroke-width="{self._stroke(width, 0, 0)}" '
                f'vector-effect="non-scaling-stroke"{dash}{ends}/></svg>')

    def picture(self, s, el, part, xf, b) -> str:
        blip = el.find(f"{P}blipFill/{A}blip")
        uri = s.d.image(part, blip.get(f"{R}embed")) if blip is not None else None
        open_, _ = self.place(xf, b, "overflow:hidden;")
        if not uri:
            target = s.d.rels(part).get(blip.get(f"{R}embed") if blip is not None else "", ("", ""))[1]
            ext = posixpath.splitext(target)[1].lstrip(".").upper() or "picture"
            return open_.replace('class="s"', 'class="s missing"') + f"{html.escape(ext)} picture</div>"
        crop = el.find(f"{P}blipFill/{A}srcRect")
        l, t, r, bt = ((_int(crop.get(k)) / 100000 for k in ("l", "t", "r", "b")) if crop is not None else (0, 0, 0, 0))
        w, h = max(0.01, 1 - l - r), max(0.01, 1 - t - bt)
        style = (f"position:absolute;left:{-l / w * 100:.2f}%;top:{-t / h * 100:.2f}%;width:{100 / w:.2f}%;"
                 f"height:{100 / h:.2f}%;max-width:none")
        flip = "transform:scaleX(-1);" if b[5] else ""
        return open_ + f'<img alt="" src="{uri}" style="{style};{flip}"></div>'

    # ------------------------------------------------------------------ frames: tables, SmartArt, charts
    def frame(self, s, el, part, xf) -> str:
        b = self.box(s, el, None)
        if b is None:
            return ""
        tbl = el.find(f".//{A}tbl")
        if tbl is not None:
            return self.table(s, tbl, xf, b)
        data, drawing = smartart_parts(s.d.z, part, el)
        shapes = list(s.d.xml(drawing).iter(f"{DSP}sp")) if drawing else []
        if shapes:
            x0, y0 = b[0], b[1]
            inner = lambda a, c, w, h: xf(x0 + a, y0 + c, w, h)
            return "".join(self.dsp(s, sp, drawing, inner) for sp in shapes)
        open_, _ = self.place(xf, b, cls="s note")
        if data:  # no drawing stored (files written by other programs): the hierarchy as a nested list
            items = smartart_lines(s.d.xml(data))
            return open_ + "<ul>" + "".join(
                f'<li style="margin-left:{(len(i) - len(i.lstrip())) * 0.6:.1f}em">{html.escape(i.strip()[2:])}</li>'
                for i in items) + "</ul></div>"
        kind = "chart" if el.find(f".//{C}chart") is not None else "embedded object"
        return open_ + f"{kind}: open the original to see it</div>"

    def dsp(self, s, sp, part, xf) -> str:
        """One shape of a SmartArt drawing (positions relative to the SmartArt frame, which `xf` adds)."""
        sppr = sp.find(f"{DSP}spPr")
        xfrm = sppr.find(f"{A}xfrm") if sppr is not None else None
        if xfrm is None or xfrm.find(f"{A}off") is None:
            return ""
        off, ext = xfrm.find(f"{A}off"), xfrm.find(f"{A}ext")
        b = (_int(off.get("x")), _int(off.get("y")), _int(ext.get("cx")), _int(ext.get("cy")),
             _int(xfrm.get("rot")) / 60000, xfrm.get("flipH") == "1", xfrm.get("flipV") == "1")
        style = sp.find(f"{DSP}style")
        prst = _preset(sppr)
        if prst in LINES or prst in ELBOWS:
            return self.connector(s, sppr, style, xf, b, prst)
        fill, (color, width) = s.fill(sppr, style, part), s.line(sppr, style)
        out = self.paint(xf, b, prst, sppr, fill, color, width)
        tx, body = sp.find(f"{DSP}txXfrm"), sp.find(f"{DSP}txBody")
        if tx is not None and tx.find(f"{A}off") is not None and body is not None and "".join(body.itertext()).strip():
            # the text box of a SmartArt shape can differ from the shape: placed on its own, unrotated by flips
            o, e = tx.find(f"{A}off"), tx.find(f"{A}ext")
            tb = (_int(o.get("x")), _int(o.get("y")), _int(e.get("cx")), _int(e.get("cy")), b[4], False, False)
            return out + "</div>" + self.place(xf, tb)[0] + self.text(s, sp, body, style, kind="other") + "</div>"
        return out + self.text(s, sp, body, style, kind="other") + "</div>"

    def table(self, s, tbl, xf, b) -> str:
        open_, _ = self.place(xf, b, "overflow:visible;")
        cols = [_int(c.get("w")) for c in tbl.iter(f"{A}gridCol")]
        total = sum(cols) or 1
        pr = tbl.find(f"{A}tblPr")
        flags = {k for k in ("firstRow", "lastRow", "firstCol", "bandRow") if pr is not None and pr.get(k) == "1"}
        sid = pr.findtext(f"{A}tableStyleId", "") if pr is not None else ""
        parts = self.table_style(s, sid)
        grid = s.color(parts.get("border")) if parts.get("border") is not None else None
        trs = list(tbl.iter(f"{A}tr"))
        rows = []
        for i, tr in enumerate(trs):
            cells = []
            for j, tc in enumerate(tr.iter(f"{A}tc")):
                if tc.get("hMerge") == "1" or tc.get("vMerge") == "1":
                    continue
                roles = ["wholeTbl"] + (["band1H"] if "bandRow" in flags and (i - ("firstRow" in flags)) % 2 == 0 else [])
                roles += [r for r, on in (("firstCol", j == 0), ("firstRow", i == 0), ("lastRow", i == len(trs) - 1)) if on and r in flags]
                tcpr = tc.find(f"{A}tcPr")
                fill = s.fill(tcpr, None, s.part) if tcpr is not None else None
                for r in reversed(roles):  # the most specific part of the table style that says something
                    fill = fill or self._style_fill(s, parts.get(r, {}).get("fill"))
                txt = [parts.get(r, {}).get("text") for r in reversed(roles) if parts.get(r, {}).get("text") is not None]
                color = next((c for c in (s.color(t) for t in txt) if c), None)
                bold = any(t.get("b") == "on" for t in txt)
                span = f' colspan="{tc.get("gridSpan")}"' if tc.get("gridSpan") else ""
                span += f' rowspan="{tc.get("rowSpan")}"' if tc.get("rowSpan") else ""
                edges = "".join(self._cell_edge(s, tcpr, k, side) for k, side in (
                    ("lnL", "left"), ("lnR", "right"), ("lnT", "top"), ("lnB", "bottom")))
                cells.append(f'<td{span} style="background:{fill or "transparent"};border-color:{grid or "#000"};{edges}">'
                             f"{self.text(s, tc, tc.find(f'{A}txBody'), None, kind='other', color=color, bold=bold, flow=True)}</td>")
            rows.append(f'<tr style="height:{self.d.u(_int(tr.get("h")))}">{"".join(cells)}</tr>')
        widths = "".join(f'<col style="width:{w * 100 / total:.2f}%">' for w in cols)
        return open_ + f'<table class="tbl"><colgroup>{widths}</colgroup>{"".join(rows)}</table></div>'

    def table_style(self, s, sid) -> dict:
        """{part: {"fill": element, "text": element}, "border": colour element} of a table style from
        ppt/tableStyles.xml; PowerPoint's default style (Medium Style 2, accent 1) when the file lacks it; a plain
        grid for any other built-in style it lacks."""
        styles = self.d.xml("ppt/tableStyles.xml") if "ppt/tableStyles.xml" in self.d.names else None
        st = next((x for x in (styles if styles is not None else []) if x.get("styleId") == sid), None)
        if st is None:
            if sid not in ("", DEFAULT_TABLE_STYLE):
                return {}
            st = ET.fromstring(MEDIUM_STYLE_2)
        out: dict = {}
        for part in st:
            out[_tag(part)] = {"fill": part.find(f"{A}tcStyle/{A}fill"), "text": part.find(f"{A}tcTxStyle")}
            if part.find(f"{A}tcStyle/{A}fillRef") is not None:
                out[_tag(part)]["fill"] = part.find(f"{A}tcStyle/{A}fillRef")
        border = st.find(f"{A}wholeTbl/{A}tcStyle/{A}tcBdr//{A}ln/{A}solidFill")
        out["border"] = border
        return out

    def _cell_edge(self, s, tcpr, key, side) -> str:
        ln = tcpr.find(f"{A}{key}") if tcpr is not None else None
        if ln is None:
            return ""
        if ln.find(f"{A}noFill") is not None:
            return f"border-{side}:none;"
        c = s.color(ln.find(f"{A}solidFill"))
        return f"border-{side}:max(1px,{self.d.u(_int(ln.get('w'), 12700))}) solid {c};" if c else ""

    @staticmethod
    def _style_fill(s, el):
        if el is None:
            return None
        if _tag(el) == "fillRef":
            return s.ref_fill(el, s.fill_styles, s.part)
        return s.fill(el, None, s.part)


    # ------------------------------------------------------------------ text
    def text(self, s, sp, body, style, kind=None, color=None, bold=False, flow=False) -> str:
        if body is None or not "".join(body.itertext()).strip():
            return ""
        kind = kind or _kind(sp)
        if sp.tag == f"{P}sp":
            own, rest = s.text_styles(sp, kind)
        else:  # SmartArt shapes and table cells: their own list style, then the presentation default
            own = [x for x in (body.find(f"{A}lstStyle"),) if x is not None]
            rest = [x for x in (s.d.default_style,) if x is not None]
        styles = own + rest
        bp = body.find(f"{A}bodyPr")
        inherited = [x.find(f"{P}txBody/{A}bodyPr") for x in (s.inherited(sp) if _tag(sp) == "sp" else [])]
        bps = [x for x in [bp] + inherited if x is not None]

        def bodyattr(k, default):
            return next((x.get(k) for x in bps if x.get(k) is not None), default)

        anchor = {"t": "flex-start", "ctr": "center", "b": "flex-end"}.get(bodyattr("anchor", "t"), "flex-start")
        ins = [self.d.u(_int(bodyattr(k, d))) for k, d in (("tIns", 45720), ("rIns", 91440), ("bIns", 45720), ("lIns", 91440))]
        fit = bp.find(f"{A}normAutofit") if bp is not None else None
        scale = _int(fit.get("fontScale"), 100000) / 100000 if fit is not None else 1
        font_ref = style.find(f"{A}fontRef") if style is not None else None
        ref_color = s.color(font_ref) if font_ref is not None else None
        theme_font = s.fonts["major" if kind == "title" else "minor"]
        paras = []
        for p in body.iter(f"{A}p"):
            ppr = p.find(f"{A}pPr")
            lvl = _int(ppr.get("lvl")) if ppr is not None else 0
            lvl_tag = f"{A}lvl{lvl + 1}pPr"
            levels = [x for x in [ppr] + [st.find(lvl_tag) for st in styles] if x is not None]
            own_levels = [x for x in [ppr] + [st.find(lvl_tag) for st in own] if x is not None]
            rest_levels = [x for x in [st.find(lvl_tag) for st in rest] if x is not None]

            def level_color(levels):
                return next((c for x in levels if x.find(f"{A}defRPr/{A}solidFill") is not None
                             and (c := s.color(x.find(f"{A}defRPr/{A}solidFill")))), None)

            def lv(get, default=None, levels=levels):  # the first level that says
                return next((v for x in levels if (v := get(x)) is not None), default)

            def defrpr(attr, levels=levels):
                return next((d.get(attr) for q in levels
                             if (d := q.find(f"{A}defRPr")) is not None and d.get(attr) is not None), None)

            algn = {"ctr": "center", "r": "right", "just": "justify", "dist": "justify"}.get(
                lv(lambda x: x.get("algn"), "l"), "left")
            base_sz = _int(defrpr("sz"), 1800)
            base_color = color or level_color(own_levels) or ref_color or level_color(rest_levels) or (
                f"#{s.colors.get(s.clr_map.get('tx1', 'dk1'), '000000')}")
            base_bold = bold or defrpr("b") == "1"
            base_italic = defrpr("i") == "1"
            mar = _int(lv(lambda x: x.get("marL"), "0"))
            indent = _int(lv(lambda x: x.get("indent"), "0"))
            bullet, bu_color = "", None
            if lv(lambda x: "none" if x.find(f"{A}buNone") is not None else None) != "none":
                ch = lv(lambda x: x.find(f"{A}buChar").get("char") if x.find(f"{A}buChar") is not None else None)
                bu_font = lv(lambda x: x.find(f"{A}buFont").get("typeface") if x.find(f"{A}buFont") is not None else None)
                if ch and bu_font and bu_font.lower().startswith(("wingdings", "symbol")):
                    ch = SYMBOL_BULLETS.get(ch, "▪")
                bu_color = lv(lambda x: s.color(x.find(f"{A}buClr")) if x.find(f"{A}buClr") is not None else None)
                if ch is None and lv(lambda x: "n" if x.find(f"{A}buAutoNum") is not None else None):
                    ch = f"{len([q for q in paras if q.startswith('<p')]) + 1}."
                bullet = ch or ""
            spc = lv(lambda x: x.find(f"{A}lnSpc/{A}spcPct").get("val") if x.find(f"{A}lnSpc/{A}spcPct") is not None else None)
            line_height = _int(spc, 100000) / 100000 * 1.2
            before = lv(lambda x: x.find(f"{A}spcBef/{A}spcPts").get("val") if x.find(f"{A}spcBef/{A}spcPts") is not None else None)
            runs, first_sz = [], None
            for r in p:
                t = _tag(r)
                if t == "br":
                    runs.append("<br>")
                    continue
                if t not in ("r", "fld"):
                    continue
                rpr = r.find(f"{A}rPr")
                sz = _int(rpr.get("sz"), base_sz) if rpr is not None and rpr.get("sz") else base_sz
                first_sz = first_sz or sz
                fill = rpr.find(f"{A}solidFill") if rpr is not None else None
                col = (s.color(fill) if fill is not None else None) or base_color
                b_ = (rpr.get("b") == "1") if rpr is not None and rpr.get("b") is not None else base_bold
                i_ = (rpr.get("i") == "1") if rpr is not None and rpr.get("i") is not None else base_italic
                u_ = rpr is not None and rpr.get("u", "none") != "none"
                latin = rpr.find(f"{A}latin") if rpr is not None else None
                face = latin.get("typeface", "") if latin is not None else ""
                face = theme_font if not face or face.startswith("+") else face
                face = re.sub(r"[^\w \-]", "", face)
                css = (f"font-size:{self.d.pt(sz / 100 * scale)};color:{col};"
                       + ("font-weight:700;" if b_ else "") + ("font-style:italic;" if i_ else "")
                       + ("text-decoration:underline;" if u_ else "") + (f"font-family:'{face}',var(--sans);" if face else ""))
                runs.append(f'<span style="{css}">{html.escape("".join(r.itertext()))}</span>')
            if not runs:  # an empty paragraph keeps its line
                end = p.find(f"{A}endParaRPr")
                sz = _int(end.get("sz"), base_sz) if end is not None else base_sz
                paras.append(f'<p style="font-size:{self.d.pt(sz / 100 * scale)};line-height:{line_height:.2f}">&nbsp;</p>')
                continue
            if re.search(r"<br>(<span[^>]*></span>)*$", "".join(runs)):
                runs.append("&#8203;")  # a line break at the end of a paragraph still makes an empty line
            bsz = (first_sz or base_sz) / 100 * scale
            bul = (f'<span class="bu" style="color:{bu_color or base_color};width:{self.d.u(max(-indent, 0))}">'
                   f"{html.escape(bullet)}</span>") if bullet else ""
            paras.append(
                f'<p style="text-align:{algn};line-height:{line_height:.2f};font-size:{self.d.pt(bsz)};'
                f'padding-left:{self.d.u(mar)};text-indent:{self.d.u(indent) if bullet else 0};'
                + (f"margin-top:{self.d.pt(_int(before) / 100 * scale)};" if before else "")
                + f'">{bul}{"".join(runs)}</p>')
        if flow:
            return "".join(paras)
        pad = " ".join(ins)
        return f'<div class="t" style="justify-content:{anchor};padding:{pad}">{"".join(paras)}</div>'


def render(data: bytes) -> list[dict]:
    """[{n, html, notes, hidden, anchor}] per slide, in presentation order."""
    return Renderer(data).slides()
