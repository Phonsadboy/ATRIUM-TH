from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "tmp" / "generated_sources"
OUT = ROOT / "public" / "art"

CHAR_SIZE = (256, 256)
PROP_SIZE = (256, 256)
SMALL_PROP_SIZE = (128, 128)
TILE_SIZE = (256, 256)
ICON_SIZE = (96, 96)
LOGO_SIZE = (512, 256)
FRAME_SIZE = (96, 96)


def ensure(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def open_rgba(path: Path) -> Image.Image:
    return Image.open(path).convert("RGBA")


def crop_cell(img: Image.Image, cols: int, rows: int, col: int, row: int) -> Image.Image:
    w, h = img.size
    x0 = round(w * col / cols)
    x1 = round(w * (col + 1) / cols)
    y0 = round(h * row / rows)
    y1 = round(h * (row + 1) / rows)
    return img.crop((x0, y0, x1, y1)).convert("RGBA")


def remove_magenta(img: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    pix = out.load()
    w, h = out.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pix[x, y]
            key_like = r > 150 and b > 140 and g < 130 and (r - g) > 65 and (b - g) > 55
            if key_like:
                pix[x, y] = (r, g, b, 0)
    return out


def alpha_mask(img: Image.Image, threshold: int = 16) -> list[bytearray]:
    a = img.getchannel("A")
    w, h = img.size
    data = list(a.getdata())
    return [bytearray(1 if data[y * w + x] > threshold else 0 for x in range(w)) for y in range(h)]


def clean_edge_components(img: Image.Image, margin: int = 2) -> Image.Image:
    mask = alpha_mask(img)
    w, h = img.size
    seen = [[False] * w for _ in range(h)]
    pix = img.load()
    for sy in range(h):
        for sx in range(w):
            if seen[sy][sx] or not mask[sy][sx]:
                continue
            q = deque([(sx, sy)])
            seen[sy][sx] = True
            comp: list[tuple[int, int]] = []
            touches_edge = False
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                # Generated sprites are foot-anchored and often legitimately
                # touch the bottom of their sheet cell. Treat top/left/right
                # edge components as grid/noise, but keep bottom-touching art.
                if x <= margin or y <= margin or x >= w - 1 - margin:
                    touches_edge = True
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and mask[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            if touches_edge:
                for x, y in comp:
                    r, g, b, _ = pix[x, y]
                    pix[x, y] = (r, g, b, 0)
    return img


def keep_largest_component(img: Image.Image) -> Image.Image:
    mask = alpha_mask(img)
    w, h = img.size
    seen = [[False] * w for _ in range(h)]
    comps: list[list[tuple[int, int]]] = []
    for sy in range(h):
        for sx in range(w):
            if seen[sy][sx] or not mask[sy][sx]:
                continue
            q = deque([(sx, sy)])
            seen[sy][sx] = True
            comp: list[tuple[int, int]] = []
            while q:
                x, y = q.popleft()
                comp.append((x, y))
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and not seen[ny][nx] and mask[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            comps.append(comp)
    if not comps:
        return img
    keep = set(max(comps, key=len))
    pix = img.load()
    for y in range(h):
        for x in range(w):
            if mask[y][x] and (x, y) not in keep:
                r, g, b, _ = pix[x, y]
                pix[x, y] = (r, g, b, 0)
    return img


def trim(img: Image.Image) -> Image.Image:
    bbox = img.getchannel("A").getbbox()
    return img.crop(bbox) if bbox else Image.new("RGBA", (1, 1), (0, 0, 0, 0))


def fit_transparent(
    img: Image.Image,
    size: tuple[int, int],
    *,
    largest: bool = False,
    max_fill: float = 0.9,
    bottom_anchor: bool = True,
) -> Image.Image:
    img = remove_magenta(img)
    if largest:
        img = keep_largest_component(img)
    img = trim(img)
    tw, th = size
    max_w = max(1, int(tw * max_fill))
    max_h = max(1, int(th * max_fill))
    scale = min(max_w / img.width, max_h / img.height)
    nw = max(1, int(round(img.width * scale)))
    nh = max(1, int(round(img.height * scale)))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    x = (tw - nw) // 2
    y = th - nh - max(1, int(th * 0.04)) if bottom_anchor else (th - nh) // 2
    out.alpha_composite(img, (x, max(0, y)))
    return out


def fit_opaque(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    img = img.convert("RGBA")
    sw, sh = img.size
    tw, th = size
    scale = max(tw / sw, th / sh)
    nw, nh = int(round(sw * scale)), int(round(sh * scale))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    x = (nw - tw) // 2
    y = (nh - th) // 2
    return img.crop((x, y, x + tw, y + th))


def save(img: Image.Image, rel: str) -> None:
    path = OUT / rel
    ensure(path)
    img.save(path)


CHAR_CELLS = [
    ("sit-idle.png", 0, 0),
    ("sit-work.png", 1, 0),
    ("sit-sleep.png", 2, 0),
    ("stand-down.png", 3, 0),
    ("walk-down-1.png", 0, 1),
    ("walk-down-2.png", 1, 1),
    ("walk-up-1.png", 2, 1),
    ("walk-up-2.png", 3, 1),
    ("walk-left-1.png", 0, 2),
    ("walk-left-2.png", 1, 2),
    ("talk-1.png", 2, 2),
    ("talk-2.png", 3, 2),
]


def process_characters() -> None:
    ids = ["exec"] + [f"char{i:02d}" for i in range(1, 11)]
    for char_id in ids:
        sheet = open_rgba(SRC / f"characters-{char_id}-sheet.png")
        first_down: Image.Image | None = None
        first_up: Image.Image | None = None
        first_left: Image.Image | None = None
        for filename, col, row in CHAR_CELLS:
            sprite = fit_transparent(crop_cell(sheet, 4, 4, col, row), CHAR_SIZE, largest=True, max_fill=0.9)
            save(sprite, f"characters/{char_id}/{filename}")
            if filename == "walk-down-1.png":
                first_down = sprite
            elif filename == "walk-up-1.png":
                first_up = sprite
            elif filename == "walk-left-1.png":
                first_left = sprite
        if first_down:
            save(first_down, f"characters/{char_id}/walk-down.png")
        if first_up:
            save(first_up, f"characters/{char_id}/walk-up.png")
        if first_left:
            save(first_left, f"characters/{char_id}/walk-left.png")


def process_desks() -> None:
    sheet = open_rgba(SRC / "furniture-desks-sheet.png")
    for i in range(10):
        sprite = fit_transparent(crop_cell(sheet, 5, 2, i % 5, i // 5), PROP_SIZE, max_fill=0.94)
        save(sprite, f"furniture/desk-{i + 1:02d}.png")


def process_props() -> None:
    sheet = open_rgba(SRC / "furniture-props-sheet.png")
    names = [
        "chair-01.png",
        "chair-02.png",
        "chair-03.png",
        "chair-04.png",
        "plant-01.png",
        "plant-02.png",
        "bookshelf.png",
        "cabinet.png",
        "whiteboard.png",
        "watercooler.png",
        "server-rack.png",
        "lamp.png",
    ]
    for i, name in enumerate(names):
        sprite = fit_transparent(
            crop_cell(sheet, 4, 3, i % 4, i // 4),
            PROP_SIZE,
            largest=name == "whiteboard.png",
            max_fill=0.9,
        )
        save(sprite, f"furniture/{name}")

    extra = open_rgba(SRC / "furniture-extra-props-sheet.png")
    extra_names = [
        ("parcel.png", SMALL_PROP_SIZE, 0),
        ("meeting-table.png", PROP_SIZE, 1),
        ("door.png", PROP_SIZE, 2),
        ("coffee-mug.png", SMALL_PROP_SIZE, 3),
        ("laptop.png", SMALL_PROP_SIZE, 4),
        ("document-stack.png", SMALL_PROP_SIZE, 5),
    ]
    for name, size, i in extra_names:
        sprite = fit_transparent(crop_cell(extra, 3, 2, i % 3, i // 3), size, max_fill=0.86)
        save(sprite, f"furniture/{name}")


def process_floor() -> None:
    sheet = open_rgba(SRC / "floor-sheet.png")
    tile_names = ["floor-tile.png", "floor-tile-alt.png", "floor-aisle.png"]
    for i, name in enumerate(tile_names):
        tile = trim(remove_magenta(crop_cell(sheet, 3, 2, i, 0)))
        tile = tile.resize(TILE_SIZE, Image.Resampling.LANCZOS)
        base = Image.new("RGBA", TILE_SIZE, (26, 22, 16, 255))
        base.alpha_composite(tile, (0, 0))
        save(base, f"floor/{name}")
    specs = [
        ("rug.png", PROP_SIZE, 0, 1, 0.94),
        ("nameplate.png", PROP_SIZE, 1, 1, 0.92),
        ("divider.png", PROP_SIZE, 2, 1, 0.92),
    ]
    for name, size, col, row, fill in specs:
        save(fit_transparent(crop_cell(sheet, 3, 2, col, row), size, max_fill=fill), f"floor/{name}")


def process_ui() -> None:
    icons = open_rgba(SRC / "ui-icons-sheet.png")
    names = [
        "chat.png",
        "tasks.png",
        "memory.png",
        "graph.png",
        "archive.png",
        "approve.png",
        "reject.png",
        "budget.png",
        "autonomy.png",
        "model.png",
        "send.png",
        "close.png",
        "plus.png",
        "pause.png",
        "play.png",
        "power.png",
        "alert.png",
        "clock.png",
    ]
    for i, name in enumerate(names):
        sprite = fit_transparent(crop_cell(icons, 6, 3, i % 6, i // 6), ICON_SIZE, max_fill=0.88, bottom_anchor=False)
        save(sprite, f"ui/icons/{name}")

    logo = fit_transparent(open_rgba(SRC / "ui-logo.png"), LOGO_SIZE, max_fill=0.9, bottom_anchor=False)
    save(logo, "ui/logo.png")

    frames = open_rgba(SRC / "ui-frames-sheet.png")
    for i, name in enumerate(["frame-panel.png", "frame-button.png", "frame-button-press.png"]):
        sprite = fit_transparent(crop_cell(frames, 3, 1, i, 0), FRAME_SIZE, max_fill=0.98, bottom_anchor=False)
        save(sprite, f"ui/{name}")


def process_backgrounds() -> None:
    for name in [
        "shared-office-main",
        "night-ops-lab",
        "maker-workshop",
        "executive-sky-lounge",
        "creative-studio",
        "garden-office",
    ]:
        img = open_rgba(SRC / f"background-{name}.png")
        save(fit_opaque(img, (1672, 941)), f"backgrounds/{name}.png")


def write_manifest() -> None:
    files = sorted(p for p in OUT.rglob("*.png"))
    lines = [
        "# ATRIUM Pixel Art Asset Manifest",
        "",
        "Generated image asset pack for `ui/public/art/`.",
        "",
        f"- Total PNG files: {len(files)}",
        f"- Characters: {len(list((OUT / 'characters').rglob('*.png')))} files",
        f"- Furniture/props: {len(list((OUT / 'furniture').rglob('*.png')))} files",
        f"- Floor/environment: {len(list((OUT / 'floor').rglob('*.png')))} files",
        f"- Room backgrounds: {len(list((OUT / 'backgrounds').rglob('*.png')))} files",
        f"- UI/logo/frame/icons: {len(list((OUT / 'ui').rglob('*.png')))} files",
        "",
        "## File List",
        "",
        "| File | Size |",
        "|---|---:|",
    ]
    for path in files:
        im = Image.open(path)
        rel = path.relative_to(OUT).as_posix()
        lines.append(f"| `{rel}` | {im.width}x{im.height} |")
    (OUT / "ASSET_MANIFEST.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    process_characters()
    process_desks()
    process_props()
    process_floor()
    process_ui()
    process_backgrounds()
    write_manifest()


if __name__ == "__main__":
    main()
