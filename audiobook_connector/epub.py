"""Parse an .epub file into an ordered list of paragraphs (no external deps)."""
from __future__ import annotations
import html as htmlmod, posixpath, re, zipfile
from dataclasses import dataclass, asdict
from xml.etree import ElementTree as ET

NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container",
      "o": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}
BLOCK = re.compile(r"<(h[1-6]|p)\b([^>]*)>(.*?)</\1>", re.S | re.I)
DIV_FALLBACK = re.compile(r"<(div|li)\b[^>]*>(.*?)</\1>", re.S | re.I)

@dataclass
class Para:
    id: int; file: str; chapter: str; tag: str; html: str; text: str

@dataclass
class Book:
    title: str; author: str; paras: list[Para]

def _clean(inner: str) -> str:
    inner = re.sub(r"<a\b[^>]*>\s*</a>", "", inner)
    inner = re.sub(r"<(img|br|hr)\b[^>]*/?>", " ", inner)
    inner = re.sub(r"<(/?)(em|i)\b[^>]*>", r"<\1i>", inner)
    inner = re.sub(r"<(/?)span\b[^>]*class=\"[^\"]*(italic|ital)[^\"]*\"[^>]*>", r"<\1i>", inner)
    inner = re.sub(r"<(/?)(strong|b)\b[^>]*>", r"<\1b>", inner)
    inner = re.sub(r"<a\b[^>]*>(.*?)</a>", r"\1", inner, flags=re.S)
    inner = re.sub(r"</?(?!/?[ib]>)[a-zA-Z][^>]*>", "", inner)   # strip every other tag
    return re.sub(r"\s+", " ", inner).strip()

def _plain(h: str) -> str:
    return htmlmod.unescape(re.sub(r"<[^>]+>", "", h)).strip()

def paras_from_html(src: str, href: str, paras: list[Para], chapter: str = "") -> str:
    """Append block elements found in one HTML document to `paras`; returns the running chapter title."""
    src = re.sub(r"<head>.*?</head>", "", src, flags=re.S | re.I)
    blocks = BLOCK.findall(src)
    if not blocks:  # div/li-based layouts
        blocks = [("p", "", inner) for _, inner in DIV_FALLBACK.findall(src) if not DIV_FALLBACK.search(inner)]
    for tag, _attrs, inner in blocks:
        h = _clean(inner); t = _plain(h)
        if not t: continue
        tag = tag.lower()
        if tag in ("h1", "h2"): chapter = t
        paras.append(Para(len(paras), href, chapter, tag, h, t))
    return chapter


def parse_epub(path: str) -> Book:
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    opf_path = container.find(".//c:rootfile", NS).get("full-path")
    opf_dir = posixpath.dirname(opf_path)
    opf = ET.fromstring(z.read(opf_path))
    title = (opf.findtext(".//dc:title", namespaces=NS) or "Untitled").strip()
    author = (opf.findtext(".//dc:creator", namespaces=NS) or "").strip()
    manifest = {i.get("id"): i.get("href") for i in opf.find("o:manifest", NS)}
    spine = [manifest[r.get("idref")] for r in opf.find("o:spine", NS) if r.get("idref") in manifest]

    paras: list[Para] = []; chapter = ""
    for href in spine:
        full = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href
        try: src = z.read(full).decode("utf-8", "replace")
        except KeyError: continue
        chapter = paras_from_html(src, href, paras, chapter)
    return Book(title, author, paras)

def book_to_dict(b: Book) -> dict:
    return {"title": b.title, "author": b.author, "paras": [asdict(p) for p in b.paras]}
