from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BLUE = "#194f7d"
RED = "#c92a2a"
GREEN = "#2b8a3e"
GREY = "#667085"


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def render_coverage_chart(path: Path, metrics: dict[str, float], title: str) -> None:
    image = Image.new("RGB", (1200, 700), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 45), title, fill=BLUE, font=_font(38, True))
    draw.text((70, 105), "Pokrycie populacji według progu", fill=GREY, font=_font(24))
    bars = [("min. 65 dB(A)", metrics["pop_ge65"], GREEN), ("min. 70 dB(A)", metrics["pop_ge70"], BLUE), ("min. 75 dB(A)", metrics["pop_ge75"], "#7b2cbf")]
    total = max(metrics["pop_total"], 1)
    for index, (label, value, color) in enumerate(bars):
        y = 205 + index * 120
        draw.text((70, y), label, fill="#17202b", font=_font(24, True))
        draw.rounded_rectangle((280, y, 1080, y + 48), radius=12, fill="#e9eef3")
        width = 800 * min(max(value / total, 0), 1)
        if width:
            draw.rounded_rectangle((280, y, 280 + width, y + 48), radius=12, fill=color)
        label_value = f"{value:,.0f} ({value / total * 100:.2f}%)".replace(",", " ").replace(".", ",")
        draw.text((300, y + 9), label_value, fill="white" if width > 250 else "#17202b", font=_font(20, True))
    outside = metrics["pop_outside_ge65"]
    draw.text((70, 590), f"Poza zasięgiem 65 dB(A): {outside:,.0f} osób".replace(",", " "), fill=RED, font=_font(28, True))
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def render_locations_map(path: Path, title: str, collections: dict[str, list[dict[str, str]]]) -> None:
    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 40), title, fill=BLUE, font=_font(34, True))
    draw.rectangle((60, 120, 1140, 710), outline="#aab4bf", width=3)
    points: list[tuple[float, float, str]] = []
    colors = {"Syreny": BLUE, "Luki": RED, "Kandydaci": GREEN}
    for label, rows in collections.items():
        lat_field, lon_field = ("lat", "lon") if label != "Kandydaci" else ("candidate_lat", "candidate_lon")
        for row in rows:
            try:
                points.append((float(row[lon_field]), float(row[lat_field]), label))
            except (KeyError, TypeError, ValueError):
                continue
    if points:
        lons = [p[0] for p in points]
        lats = [p[1] for p in points]
        x_min, x_max = min(lons), max(lons)
        y_min, y_max = min(lats), max(lats)
        x_span = max(x_max - x_min, 0.02)
        y_span = max(y_max - y_min, 0.02)
        for lon, lat, label in points:
            x = 100 + (lon - x_min + (x_span - (x_max - x_min)) / 2) / x_span * 1000
            y = 680 - (lat - y_min + (y_span - (y_max - y_min)) / 2) / y_span * 520
            radius = 7 if label == "Syreny" else 10
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colors[label], outline="white")
    else:
        draw.text((330, 390), "Brak lokalizacji w wybranej jednostce", fill=GREY, font=_font(28))
    x = 70
    for label in ("Syreny", "Luki", "Kandydaci"):
        draw.ellipse((x, 740, x + 18, 758), fill=colors[label])
        draw.text((x + 28, 735), label, fill="#17202b", font=_font(19))
        x += 200
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def render_geopackage_map(path: Path, title: str, geopackage: Path) -> bool:
    try:
        import geopandas as gpd
    except ImportError:
        return False
    boundary = gpd.read_file(geopackage, layer="admin_boundary", engine="pyogrio").to_crs(2180)
    layers = {
        "Syreny": gpd.read_file(geopackage, layer="sirens", engine="pyogrio").to_crs(2180),
        "Luki": gpd.read_file(geopackage, layer="priority_gaps", engine="pyogrio").to_crs(2180),
        "Kandydaci": gpd.read_file(geopackage, layer="candidates", engine="pyogrio").to_crs(2180),
    }
    if boundary.empty:
        return False
    min_x, min_y, max_x, max_y = boundary.total_bounds
    pad = 10_000
    min_x, min_y, max_x, max_y = min_x - pad, min_y - pad, max_x + pad, max_y + pad
    span_x, span_y = max(max_x - min_x, 1), max(max_y - min_y, 1)

    def project(x: float, y: float) -> tuple[float, float]:
        return 90 + (x - min_x) / span_x * 1020, 700 - (y - min_y) / span_y * 550

    image = Image.new("RGB", (1200, 800), "white")
    draw = ImageDraw.Draw(image)
    draw.text((60, 35), title, fill=BLUE, font=_font(34, True))
    draw.rectangle((60, 110, 1140, 720), fill="#f7f9fb", outline="#aab4bf", width=2)
    for geometry in boundary.geometry:
        polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
        for polygon in polygons:
            exterior = [project(x, y) for x, y in polygon.exterior.coords]
            draw.polygon(exterior, fill="#dbeafe", outline=BLUE, width=3)
            for interior in polygon.interiors:
                draw.polygon([project(x, y) for x, y in interior.coords], fill="#f7f9fb")
    colors = {"Syreny": BLUE, "Luki": RED, "Kandydaci": GREEN}
    for label, frame in layers.items():
        for geometry in frame.geometry:
            point = geometry if geometry.geom_type == "Point" else geometry.representative_point()
            x, y = project(point.x, point.y)
            radius = 4 if label == "Syreny" else 7
            draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colors[label], outline="white")
    x = 70
    for label in ("Syreny", "Luki", "Kandydaci"):
        draw.ellipse((x, 752, x + 18, 770), fill=colors[label])
        draw.text((x + 28, 747), label, fill="#17202b", font=_font(19))
        x += 200
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return True


def render_markdown_pdf(markdown_path: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    stylesheet = markdown_path.parent / "report-style.css"
    stylesheet.write_text(
        "@page { size: A4; margin: 18mm 15mm 18mm 15mm; }\n"
        "body { font-family: 'Noto Sans', sans-serif; font-size: 10pt; color: #17202b; }\n"
        "h1, h2, h3 { color: #194f7d; page-break-after: avoid; }\n"
        "table { width: 100%; border-collapse: collapse; font-size: 8.5pt; }\n"
        "th, td { border: 0.4pt solid #aab4bf; padding: 3pt; overflow-wrap: anywhere; }\n"
        "tr, img { page-break-inside: avoid; } img { max-width: 100%; height: auto; }\n",
        encoding="utf-8",
    )
    command = [
        "pandoc", str(markdown_path), "--standalone", "--from=gfm", "--pdf-engine=weasyprint",
        "--variable", "pagetitle=Raport SOIA", "--css", str(stylesheet), "--output", str(output)
    ]
    try:
        subprocess.run(command, cwd=markdown_path.parent, check=True)
    except FileNotFoundError as error:
        raise RuntimeError("Eksport PDF wymaga poleceń pandoc i weasyprint") from error
