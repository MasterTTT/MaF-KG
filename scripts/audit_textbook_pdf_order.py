"""Render source PDF evidence without rewriting source text or graph artifacts."""
import argparse
import hashlib
import json
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render(pdf, out, pages):
    out.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(pdf)
    records = []
    tiles = []
    for number in pages:
        if not 1 <= number <= len(doc):
            raise ValueError("Page outside PDF")
        page = doc[number - 1]
        pix = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        target = out / f"page_{number:03d}.png"
        pix.save(target)
        records.append({"pdf_page": number, "path": str(target), "sha256": sha(target),
                        "text_layer_chars": len(page.get_text()), "page_label": page.get_label()})
        im = Image.open(target).convert("RGB")
        im.thumbnail((270, 360))
        tile = Image.new("RGB", (280, 390), "white")
        tile.paste(im, ((280-im.width)//2, 25))
        ImageDraw.Draw(tile).text((8, 6), f"PDF page {number}", fill="black")
        tiles.append(tile)
    for start in range(0, len(tiles), 20):
        subset = tiles[start:start+20]
        sheet = Image.new("RGB", (280*4, 390*((len(subset)+3)//4)), "#dddddd")
        for i, tile in enumerate(subset):
            sheet.paste(tile, ((i%4)*280, (i//4)*390))
        sheet.save(out / f"contact_{start//20+1}.png")
    (out / "render_manifest.json").write_text(json.dumps({"source": str(pdf), "sha256": sha(pdf),
        "page_count": len(doc), "rendered": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Rendered", pdf.name, "pages", list(pages), "to", out)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("pdf", type=Path)
    p.add_argument("out", type=Path)
    p.add_argument("--start", type=int, required=True)
    p.add_argument("--end", type=int, required=True)
    a = p.parse_args()
    render(a.pdf.resolve(), a.out.resolve(), range(a.start, a.end + 1))
