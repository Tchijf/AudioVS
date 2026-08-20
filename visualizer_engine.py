from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


@dataclass
class RenderSettings:
    bars: int = 60
    width_pct: int = 82
    bar_width_pct: int = 72
    height_pct: int = 24
    vertical_position_pct: int = 50
    opacity_pct: int = 92
    sensitivity: float = 1.8
    averaging: int = 3
    mirrored: bool = True
    glow: bool = True
    visualizer_style: str = "Klassische Balken"
    bass_weight_pct: int = 100
    mid_weight_pct: int = 100
    treble_weight_pct: int = 100
    smoothness_pct: int = 35

    show_logo: bool = True
    logo_size_pct: int = 24
    logo_content_scale_pct: int = 100
    logo_x_pct: int = 50
    logo_y_pct: int = 50
    remove_black_logo_bg: bool = True

    show_cover: bool = False
    cover_size_pct: int = 22
    cover_x_pct: int = 50
    cover_y_pct: int = 28
    cover_effect: str = "Schatten"
    cover_corner_pct: int = 6
    cover_shadow_strength_pct: int = 55
    cover_shadow_angle_deg: int = 135
    cover_shadow_distance_pct: int = 5
    cover_frame_width_pct: int = 0
    cover_frame_color: str = "#ffffff"
    cover_glow_strength_pct: int = 0
    cover_glow_color: str = "#a855f7"
    glass_enabled: bool = False
    glass_opacity_pct: int = 28

    show_text: bool = True
    title: str = ""
    artist: str = ""
    font_name: str = "Segoe UI"
    title_size_pct: int = 5
    artist_size_pct: int = 3
    title_x_pct: int = 50
    title_y_pct: int = 82
    artist_x_pct: int = 50
    artist_y_pct: int = 88
    title_bold: bool = True
    artist_bold: bool = False
    title_uppercase: bool = False
    artist_uppercase: bool = False
    title_letter_spacing: int = 0
    artist_letter_spacing: int = 0
    text_shadow_pct: int = 55
    text_glow_pct: int = 0
    title_color: str = "#ffffff"
    artist_color: str = "#d8d8ff"

    # Backwards-compatible legacy text settings.
    text_size_pct: int = 4
    text_x_pct: int = 50
    text_y_pct: int = 86

    color_left: str = "#ff2bd6"
    color_mid: str = "#a855f7"
    color_right: str = "#00e5ff"
    video_preset: str = "medium"
    crf: int = 18


class VisualizerError(RuntimeError):
    pass


def design_reference_size(width: int, height: int, max_dimension: int = 1920) -> tuple[int, int]:
    """Stable WYSIWYG design canvas used by both preview and final artwork.

    Artwork (logo, cover, text, shadows/glow) is rendered on this reference
    canvas and then scaled as one layer to the export resolution. That removes
    font/stroke/layout differences caused by composing the UI preview at a much
    smaller resolution than the final MP4.
    """
    width = max(2, int(width))
    height = max(2, int(height))
    longest = max(width, height)
    if longest <= max_dimension:
        return width, height
    scale = max_dimension / float(longest)
    w = max(2, int(round(width * scale)))
    h = max(2, int(round(height * scale)))
    if w % 2:
        w += 1
    if h % 2:
        h += 1
    return w, h


def compute_visualizer_layout(width: int, height: int, settings: RenderSettings) -> dict:
    """One geometry source of truth for static preview and FFmpeg export."""
    bars = max(12, min(160, int(settings.bars)))
    vis_w = max(80, min(width, int(round(width * settings.width_pct / 100.0))))
    if vis_w % 2:
        vis_w -= 1
    vis_w = max(2, vis_w)
    total_h = max(30, min(int(round(height * 0.70)), int(round(height * settings.height_pct / 100.0))))
    if total_h % 2:
        total_h += 1
    center_y = int(round(height * max(0, min(110, settings.vertical_position_pct)) / 100.0))
    x = int(round((width - vis_w) / 2.0))
    # Deliberately do not clamp: Pillow and FFmpeg both clip naturally at the
    # image edge. Clamping in only one path was a source of preview/export drift.
    y_centered = int(round(center_y - total_h / 2.0))
    # A one-sided frequency visualizer grows upward from its baseline.
    # FFmpeg's showfreqs anchors bars to the bottom edge of its own canvas,
    # so its overlay must start a full visualizer height above the baseline.
    # Older versions incorrectly used the centered y value here during export,
    # which shifted non-mirrored visualizers down by half their height.
    y_one_sided = int(round(center_y - total_h))
    return {
        "bars": bars,
        "width": vis_w,
        "height": total_h,
        "center_y": center_y,
        "x": x,
        "y": y_centered,
        "y_centered": y_centered,
        "y_one_sided": y_one_sided,
        "half_height": max(15, total_h // 2),
    }


def _creationflags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def find_binary(name: str) -> Optional[str]:
    """Locate the bundled video-engine binary.

    Release builds ship FFmpeg inside the PyInstaller bundle under ``engine``.
    Development/portable source runs may keep the same files next to the app in
    ``engine`` or ``ffmpeg``.  We deliberately do not use per-user FFmpeg paths
    or download helpers in production so the application is fully offline.
    """
    filenames = [f"{name}.exe", name] if os.name == "nt" else [name, f"{name}.exe"]
    code_base = Path(__file__).resolve().parent
    app_base = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else code_base
    meipass = Path(getattr(sys, "_MEIPASS", app_base))

    bases = [
        meipass / "engine",
        app_base / "engine",
        app_base / "ffmpeg",
        code_base / "engine",
        code_base / "ffmpeg",
    ]

    seen: set[str] = set()
    for base in bases:
        for filename in filenames:
            candidate = base / filename
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            if candidate.is_file():
                return str(candidate.resolve())

    # Development fallback only. Installed release builds should always resolve
    # the bundled engine above.
    found = shutil.which(name)
    if found and Path(found).is_file():
        return str(Path(found).resolve())
    return None

def probe_media(video_path: str, ffprobe_path: str) -> dict:
    cmd = [
        ffprobe_path,
        "-v", "error",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        video_path,
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creationflags(),
    )
    if result.returncode != 0:
        raise VisualizerError(result.stderr.strip() or "Video konnte nicht analysiert werden.")
    try:
        info = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise VisualizerError("ffprobe hat keine gültigen Mediendaten geliefert.") from exc

    streams = info.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not vstream:
        raise VisualizerError("Die Datei enthält keine Videospur.")
    if not astream:
        raise VisualizerError("Die Datei enthält keine Audiospur. Ein Audio-Visualizer benötigt Audio im Video.")

    width = int(vstream.get("width") or 0)
    height = int(vstream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise VisualizerError("Videoauflösung konnte nicht ermittelt werden.")

    duration = 0.0
    for value in (vstream.get("duration"), astream.get("duration"), info.get("format", {}).get("duration")):
        try:
            if value is not None:
                duration = max(duration, float(value))
        except (TypeError, ValueError):
            pass
    if duration <= 0:
        raise VisualizerError("Videolänge konnte nicht ermittelt werden.")

    fps = 30.0
    rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate")
    if rate and rate != "0/0":
        try:
            n, d = rate.split("/")
            if float(d) != 0:
                fps = float(n) / float(d)
        except Exception:
            pass
    fps = min(max(fps, 10.0), 120.0)

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "fps": fps,
        "video_codec": vstream.get("codec_name", ""),
        "audio_codec": astream.get("codec_name", ""),
    }


def _hex_rgb(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        raise VisualizerError(f"Ungültige Farbe: {value}")
    try:
        rgb = tuple(int(raw[i:i + 2], 16) for i in (0, 2, 4))
        return rgb  # type: ignore[return-value]
    except ValueError as exc:
        raise VisualizerError(f"Ungültige Farbe: {value}") from exc


def _mix_color_expr(hex_color: str) -> str:
    r, g, b = _hex_rgb(hex_color)
    return f"rr={r/255:.5f}:gg={g/255:.5f}:bb={b/255:.5f}"


def _lerp(a: int, b: int, t: float) -> int:
    return int(round(a + (b - a) * t))


def _gradient_color(stops: list[tuple[int, int, int]], t: float) -> tuple[int, int, int]:
    t = min(max(t, 0.0), 1.0)
    if len(stops) == 1:
        return stops[0]
    pos = t * (len(stops) - 1)
    idx = min(int(pos), len(stops) - 2)
    local_t = pos - idx
    a, b = stops[idx], stops[idx + 1]
    return tuple(_lerp(a[i], b[i], local_t) for i in range(3))  # type: ignore[return-value]


def _prepare_logo_source(logo_path: str, remove_black: bool) -> Image.Image:
    try:
        img = Image.open(logo_path).convert("RGBA")
    except Exception as exc:
        raise VisualizerError(f"Logo konnte nicht geöffnet werden: {exc}") from exc

    if remove_black:
        px = img.load()
        for y in range(img.height):
            for x in range(img.width):
                r, g, b, a = px[x, y]
                brightness = max(r, g, b)
                if brightness < 30:
                    px[x, y] = (r, g, b, 0)
                elif brightness < 75:
                    new_a = int(a * (brightness - 30) / 45)
                    px[x, y] = (r, g, b, max(0, min(255, new_a)))
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    return img


def make_logo_badge_image(
    logo_path: str,
    size: int,
    color_left: str,
    color_mid: str,
    color_right: str,
    remove_black: bool = True,
    logo_content_scale_pct: int = 100,
) -> Image.Image:
    """Create the circular logo badge in memory."""
    size = max(64, int(size))
    scale = 3
    s = size * scale
    left = _hex_rgb(color_left)
    mid = _hex_rgb(color_mid)
    right = _hex_rgb(color_right)

    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    disc = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ddraw = ImageDraw.Draw(disc)
    margin = int(s * 0.075)
    ddraw.ellipse((margin, margin, s - margin, s - margin), fill=(4, 6, 12, 220))
    canvas = Image.alpha_composite(canvas, disc)

    ring_mask = Image.new("L", (s, s), 0)
    rdraw = ImageDraw.Draw(ring_mask)
    outer_m = int(s * 0.035)
    ring_w = max(5, int(s * 0.022))
    rdraw.ellipse((outer_m, outer_m, s - outer_m, s - outer_m), outline=255, width=ring_w)

    gradient = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    gp = gradient.load()
    mask_px = ring_mask.load()
    stops = [left, mid, right]
    for x in range(s):
        col = _gradient_color(stops, x / max(1, s - 1))
        for y in range(s):
            a = mask_px[x, y]
            if a:
                gp[x, y] = (*col, a)

    glow_mask = ring_mask.filter(ImageFilter.GaussianBlur(radius=max(3, int(s * 0.03))))
    glow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    glow_px = glow.load()
    gm = glow_mask.load()
    for x in range(s):
        col = _gradient_color(stops, x / max(1, s - 1))
        for y in range(s):
            a = gm[x, y]
            if a:
                glow_px[x, y] = (*col, int(a * 0.55))
    canvas = Image.alpha_composite(canvas, glow)
    canvas = Image.alpha_composite(canvas, gradient)

    logo = _prepare_logo_source(logo_path, remove_black)
    content_scale = max(20, min(180, int(logo_content_scale_pct))) / 100.0
    max_w = int(s * 0.66 * content_scale)
    max_h = int(s * 0.42 * content_scale)
    ratio = min(max_w / max(1, logo.width), max_h / max(1, logo.height))
    new_size = (max(1, int(logo.width * ratio)), max(1, int(logo.height * ratio)))
    logo = logo.resize(new_size, Image.Resampling.LANCZOS)
    lx = (s - logo.width) // 2
    ly = (s - logo.height) // 2
    canvas.alpha_composite(logo, (lx, ly))

    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def make_logo_badge(
    logo_path: str,
    output_path: str,
    size: int,
    color_left: str,
    color_mid: str,
    color_right: str,
    remove_black: bool = True,
    logo_content_scale_pct: int = 100,
) -> None:
    badge = make_logo_badge_image(
        logo_path=logo_path,
        size=size,
        color_left=color_left,
        color_mid=color_mid,
        color_right=color_right,
        remove_black=remove_black,
        logo_content_scale_pct=logo_content_scale_pct,
    )
    badge.save(output_path, "PNG")



def _font_file(font_name: str, bold: bool = False) -> Path | None:
    name = (font_name or "Segoe UI").strip().lower()
    windows_map = {
        "segoe ui": ("segoeui.ttf", "segoeuib.ttf"),
        "arial": ("arial.ttf", "arialbd.ttf"),
        "calibri": ("calibri.ttf", "calibrib.ttf"),
        "verdana": ("verdana.ttf", "verdanab.ttf"),
        "georgia": ("georgia.ttf", "georgiab.ttf"),
        "times new roman": ("times.ttf", "timesbd.ttf"),
        "impact": ("impact.ttf", "impact.ttf"),
        "trebuchet ms": ("trebuc.ttf", "trebucbd.ttf"),
    }
    windir = os.environ.get("WINDIR")
    if windir:
        font_dir = Path(windir) / "Fonts"
        normal_file, bold_file = windows_map.get(name, windows_map["segoe ui"])
        candidate = font_dir / (bold_file if bold else normal_file)
        if candidate.is_file():
            return candidate
    linux = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    if linux.is_file():
        return linux
    mac_candidates = [
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Verdana Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Verdana.ttf"),
    ]
    return next((c for c in mac_candidates if c.is_file()), None)


def _load_font(size: int, bold: bool = False, font_name: str = "Segoe UI") -> ImageFont.ImageFont:
    size = max(10, int(size))
    candidate = _font_file(font_name, bold)
    if candidate:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _spaced_text_metrics(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, spacing: int) -> tuple[int, int]:
    if not text:
        return 0, 0
    spacing = int(spacing)
    widths = []
    max_h = 0
    for ch in text:
        box = draw.textbbox((0, 0), ch, font=font)
        widths.append(max(0, box[2] - box[0]))
        max_h = max(max_h, max(0, box[3] - box[1]))
    return sum(widths) + spacing * max(0, len(text) - 1), max_h


def _draw_spaced_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    font: ImageFont.ImageFont,
    fill,
    spacing: int = 0,
    stroke_width: int = 0,
    stroke_fill=None,
) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill, stroke_width=stroke_width, stroke_fill=stroke_fill)
        box = draw.textbbox((0, 0), ch, font=font, stroke_width=stroke_width)
        x += max(1, box[2] - box[0]) + spacing


def make_cover_image(cover_path: str, size: int) -> Image.Image:
    try:
        with Image.open(cover_path) as src:
            image = src.convert("RGBA")
    except Exception as exc:
        raise VisualizerError(f"Cover konnte nicht geöffnet werden: {exc}") from exc
    size = max(48, int(size))
    return ImageOps.fit(image, (size, size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _rounded_cover(image: Image.Image, corner_pct: int) -> Image.Image:
    image = image.convert("RGBA")
    radius = int(min(image.size) * max(0, min(50, corner_pct)) / 100)
    if radius <= 0:
        return image
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius=radius, fill=255)
    image.putalpha(mask)
    return image


def _centered_xy(width: int, height: int, item_w: int, item_h: int, x_pct: int, y_pct: int) -> tuple[int, int]:
    cx = int(width * max(0, min(100, x_pct)) / 100)
    cy = int(height * max(0, min(110, y_pct)) / 100)
    x = cx - item_w // 2
    y = cy - item_h // 2
    x = max(-item_w // 2, min(width - item_w // 2, x))
    y = max(-item_h // 2, min(height - item_h // 2, y))
    return x, y


def _text_layout_one(width: int, height: int, settings: RenderSettings, which: str) -> dict | None:
    raw = (settings.title if which == "title" else settings.artist) or ""
    raw = raw.strip()
    if not raw:
        return None
    uppercase = settings.title_uppercase if which == "title" else settings.artist_uppercase
    text = raw.upper() if uppercase else raw
    size_pct = settings.title_size_pct if which == "title" else settings.artist_size_pct
    bold = settings.title_bold if which == "title" else settings.artist_bold
    spacing = settings.title_letter_spacing if which == "title" else settings.artist_letter_spacing
    x_pct = settings.title_x_pct if which == "title" else settings.artist_x_pct
    y_pct = settings.title_y_pct if which == "title" else settings.artist_y_pct
    px_size = max(11, int(min(width, height) * max(1, min(15, size_pct)) / 100))
    font = _load_font(px_size, bold=bold, font_name=settings.font_name)
    dummy = Image.new("RGBA", (max(1, width), max(1, height)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(dummy)
    text_w, text_h = _spaced_text_metrics(draw, text, font, spacing)
    max_w = int(width * 0.94)
    while text_w > max_w and px_size > 10:
        px_size -= 1
        font = _load_font(px_size, bold=bold, font_name=settings.font_name)
        text_w, text_h = _spaced_text_metrics(draw, text, font, spacing)
    cx = int(width * max(0, min(100, x_pct)) / 100)
    cy = int(height * max(0, min(110, y_pct)) / 100)
    x = cx - text_w // 2
    y = cy - text_h // 2
    return {"x": x, "y": y, "w": text_w, "h": text_h, "font": font, "text": text, "spacing": spacing, "size": px_size}


def measure_artwork_layout(
    width: int,
    height: int,
    settings: RenderSettings,
    logo_path: Optional[str] = None,
    cover_path: Optional[str] = None,
) -> dict:
    result: dict[str, dict] = {}
    if settings.show_cover and cover_path and os.path.isfile(cover_path):
        cover_size = int(min(width, height) * max(6, min(65, settings.cover_size_pct)) / 100)
        cover_size = max(48, min(int(min(width, height) * 0.72), cover_size))
        x, y = _centered_xy(width, height, cover_size, cover_size, settings.cover_x_pct, settings.cover_y_pct)
        result["cover"] = {"x": x, "y": y, "w": cover_size, "h": cover_size}
    if settings.show_logo and logo_path and os.path.isfile(logo_path):
        badge_size = int(min(width, height) * max(8, min(55, settings.logo_size_pct)) / 100)
        badge_size = max(64, min(int(min(width, height) * 0.62), badge_size))
        x, y = _centered_xy(width, height, badge_size, badge_size, settings.logo_x_pct, settings.logo_y_pct)
        result["logo"] = {"x": x, "y": y, "w": badge_size, "h": badge_size}
    if settings.show_text:
        title = _text_layout_one(width, height, settings, "title")
        artist = _text_layout_one(width, height, settings, "artist")
        if title:
            result["title"] = {k: title[k] for k in ("x", "y", "w", "h")}
        if artist:
            result["artist"] = {k: artist[k] for k in ("x", "y", "w", "h")}
        if title or artist:
            boxes = [b for b in (title, artist) if b]
            x0 = min(b["x"] for b in boxes)
            y0 = min(b["y"] for b in boxes)
            x1 = max(b["x"] + b["w"] for b in boxes)
            y1 = max(b["y"] + b["h"] for b in boxes)
            result["text"] = {"x": x0, "y": y0, "w": x1-x0, "h": y1-y0}
    return result


def _add_glass_panel(overlay: Image.Image, box: tuple[int,int,int,int], opacity_pct: int, radius: int = 18) -> None:
    x0, y0, x1, y1 = box
    pad = max(8, int(min(overlay.size) * 0.012))
    x0 -= pad; y0 -= pad; x1 += pad; y1 += pad
    panel = Image.new("RGBA", overlay.size, (0,0,0,0))
    draw = ImageDraw.Draw(panel)
    alpha = int(255 * max(0, min(80, opacity_pct)) / 100)
    draw.rounded_rectangle((x0, y0, x1, y1), radius=radius, fill=(18, 25, 44, alpha), outline=(255,255,255,min(110, alpha+30)), width=1)
    overlay.alpha_composite(panel)


def make_artwork_overlay(
    width: int,
    height: int,
    settings: RenderSettings,
    logo_path: Optional[str] = None,
    cover_path: Optional[str] = None,
    badge_override: Optional[Image.Image] = None,
    cover_override: Optional[Image.Image] = None,
) -> Image.Image:
    import math
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    layout = measure_artwork_layout(width, height, settings, logo_path=logo_path, cover_path=cover_path)

    if settings.glass_enabled:
        if "cover" in layout:
            c = layout["cover"]
            _add_glass_panel(overlay, (c["x"], c["y"], c["x"]+c["w"], c["y"]+c["h"]), settings.glass_opacity_pct, radius=max(12, c["w"]//14))
        text_boxes = [layout[k] for k in ("title", "artist") if k in layout]
        if text_boxes:
            x0=min(b["x"] for b in text_boxes); y0=min(b["y"] for b in text_boxes)
            x1=max(b["x"]+b["w"] for b in text_boxes); y1=max(b["y"]+b["h"] for b in text_boxes)
            _add_glass_panel(overlay, (x0,y0,x1,y1), settings.glass_opacity_pct, radius=max(12, int(min(width,height)*0.02)))

    if "cover" in layout and settings.show_cover and cover_path and os.path.isfile(cover_path):
        box = layout["cover"]
        cover_size = box["w"]
        cover = cover_override.convert("RGBA") if cover_override is not None else make_cover_image(cover_path, cover_size)
        if cover.size != (cover_size, cover_size):
            cover = cover.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
        cover = _rounded_cover(cover, settings.cover_corner_pct)
        x, y = box["x"], box["y"]

        # Glow behind cover.
        glow_strength = max(0, min(100, settings.cover_glow_strength_pct))
        if glow_strength > 0:
            pad = max(12, int(cover_size * 0.12))
            mask = Image.new("L", (cover_size + pad*2, cover_size + pad*2), 0)
            radius = int(cover_size * max(0, min(50, settings.cover_corner_pct)) / 100) + pad//2
            ImageDraw.Draw(mask).rounded_rectangle((pad, pad, pad+cover_size-1, pad+cover_size-1), radius=radius, fill=int(255*glow_strength/100))
            mask = mask.filter(ImageFilter.GaussianBlur(radius=max(5, int(cover_size*0.06))))
            color = _hex_rgb(settings.cover_glow_color)
            glow = Image.new("RGBA", mask.size, (*color, 0)); glow.putalpha(mask)
            overlay.alpha_composite(glow, (x-pad, y-pad))

        # Directional shadow.
        shadow_strength = max(0, min(100, settings.cover_shadow_strength_pct))
        if shadow_strength > 0:
            dist = int(cover_size * max(0, min(30, settings.cover_shadow_distance_pct)) / 100)
            angle = math.radians(settings.cover_shadow_angle_deg)
            dx = int(math.cos(angle) * dist); dy = int(math.sin(angle) * dist)
            pad = max(14, int(cover_size*0.1))
            mask = Image.new("L", (cover_size+pad*2, cover_size+pad*2), 0)
            radius = int(cover_size * max(0, min(50, settings.cover_corner_pct)) / 100)
            ImageDraw.Draw(mask).rounded_rectangle((pad,pad,pad+cover_size-1,pad+cover_size-1), radius=radius, fill=int(210*shadow_strength/100))
            mask=mask.filter(ImageFilter.GaussianBlur(radius=max(2,int(cover_size*0.035))))
            shadow=Image.new("RGBA",mask.size,(0,0,0,0)); shadow.putalpha(mask)
            overlay.alpha_composite(shadow,(x-pad+dx,y-pad+dy))

        # Optional frame.
        fw = int(cover_size * max(0, min(12, settings.cover_frame_width_pct)) / 100)
        if fw > 0:
            framed = Image.new("RGBA", cover.size, (0,0,0,0))
            framed.alpha_composite(cover)
            fd = ImageDraw.Draw(framed)
            radius = int(cover_size * max(0, min(50, settings.cover_corner_pct)) / 100)
            fd.rounded_rectangle((fw//2,fw//2,cover_size-1-fw//2,cover_size-1-fw//2), radius=radius, outline=(*_hex_rgb(settings.cover_frame_color),255), width=fw)
            cover = framed
        overlay.alpha_composite(cover, (x, y))

    if "logo" in layout and settings.show_logo and logo_path and os.path.isfile(logo_path):
        box = layout["logo"]
        badge_size = box["w"]
        badge = badge_override.convert("RGBA") if badge_override is not None else make_logo_badge_image(
            logo_path=logo_path, size=badge_size,
            color_left=settings.color_left, color_mid=settings.color_mid, color_right=settings.color_right,
            remove_black=settings.remove_black_logo_bg, logo_content_scale_pct=settings.logo_content_scale_pct,
        )
        if badge.size != (badge_size, badge_size):
            badge = badge.resize((badge_size, badge_size), Image.Resampling.LANCZOS)
        overlay.alpha_composite(badge, (box["x"], box["y"]))

    if settings.show_text:
        for which in ("title", "artist"):
            info = _text_layout_one(width, height, settings, which)
            if not info:
                continue
            color_hex = settings.title_color if which == "title" else settings.artist_color
            color = (*_hex_rgb(color_hex), 245)
            stroke = max(0, int(info["size"] * max(0, min(100, settings.text_shadow_pct)) / 100 / 10))
            if settings.text_glow_pct > 0:
                layer = Image.new("RGBA", overlay.size, (0,0,0,0)); ld = ImageDraw.Draw(layer)
                _draw_spaced_text(ld, (info["x"],info["y"]), info["text"], info["font"], color, info["spacing"], 0, None)
                alpha = layer.getchannel("A").point(lambda a: int(a*max(0,min(100,settings.text_glow_pct))/100))
                glow = Image.new("RGBA",overlay.size,color); glow.putalpha(alpha.filter(ImageFilter.GaussianBlur(radius=max(3,int(info["size"]*0.18)))))
                overlay.alpha_composite(glow)
            draw=ImageDraw.Draw(overlay)
            _draw_spaced_text(draw,(info["x"],info["y"]),info["text"],info["font"],color,info["spacing"],stroke,(0,0,0,200) if stroke else None)
    return overlay


def _sample_preview_amplitudes(count: int, settings: RenderSettings) -> list[float]:
    import math
    bass = max(0, min(200, settings.bass_weight_pct))/100
    mid = max(0, min(200, settings.mid_weight_pct))/100
    treble = max(0, min(200, settings.treble_weight_pct))/100
    result=[]
    for i in range(max(1,count)):
        t=i/max(1,count-1)
        w = bass*(1-t)**2 + mid*(1-abs(t-.5)*2) + treble*t*t
        peak1=math.exp(-((t-.18)/.10)**2); peak2=.78*math.exp(-((t-.54)/.15)**2); peak3=.92*math.exp(-((t-.79)/.085)**2)
        ripple=.12*(.5+.5*math.sin(i*1.83+.7))
        amp=(.12+.5*peak1+.4*peak2+.46*peak3+ripple)*(0.62+max(.5,min(4,settings.sensitivity))*.22)*max(.25,w)
        result.append(min(1.0,amp))
    return result


def make_static_preview(
    base_frame: Image.Image,
    settings: RenderSettings,
    logo_path: Optional[str] = None,
    cover_path: Optional[str] = None,
    badge_override: Optional[Image.Image] = None,
    cover_override: Optional[Image.Image] = None,
) -> Image.Image:
    import math
    base=base_frame.convert("RGBA"); width,height=base.size
    geom=compute_visualizer_layout(width,height,settings)
    bars=geom["bars"]
    vis_w=geom["width"]
    total_h=geom["height"]
    center_y=geom["center_y"]
    x0=geom["x"]
    stops=[_hex_rgb(settings.color_left),_hex_rgb(settings.color_mid),_hex_rgb(settings.color_right)]
    opacity=max(20,min(255,int(255*settings.opacity_pct/100)))
    amps=_sample_preview_amplitudes(bars,settings)
    visual=Image.new("RGBA",base.size,(0,0,0,0)); draw=ImageDraw.Draw(visual)
    style=(settings.visualizer_style or "Klassische Balken").lower()

    if "kreis" in style or "ring" in style:
        if "ring" in style and settings.show_logo:
            cx=int(width*settings.logo_x_pct/100); cy=int(height*settings.logo_y_pct/100)
            base_r=int(min(width,height)*settings.logo_size_pct/100*.58)
        else:
            cx=width//2; cy=center_y; base_r=max(25,int(total_h*.55))
        extra=max(8,int(total_h*.42))
        for i,amp in enumerate(amps):
            a=2*math.pi*i/len(amps)-math.pi/2; r1=base_r; r2=base_r+extra*amp
            col=_gradient_color(stops,i/max(1,len(amps)-1)); fill=(*col,opacity)
            draw.line((cx+math.cos(a)*r1,cy+math.sin(a)*r1,cx+math.cos(a)*r2,cy+math.sin(a)*r2),fill=fill,width=max(1,int(vis_w/bars*.45)))
    elif "waveform" in style:
        points=[]
        for i,amp in enumerate(amps):
            x=x0+i*vis_w/max(1,bars-1); y=center_y-math.sin(i*.68)*amp*total_h*.42
            points.append((x,y))
        for i in range(len(points)-1):
            col=(*_gradient_color(stops,i/max(1,len(points)-2)),opacity)
            draw.line((*points[i],*points[i+1]),fill=col,width=max(2,int(vis_w/bars*.35)))
    elif "doppelwelle" in style:
        top=[]; bottom=[]
        for i,amp in enumerate(amps):
            x=x0+i*vis_w/max(1,bars-1); v=math.sin(i*.72)*amp*total_h*.38
            top.append((x,center_y-v)); bottom.append((x,center_y+v))
        for pts in (top,bottom):
            for i in range(len(pts)-1):
                col=(*_gradient_color(stops,i/max(1,len(pts)-2)),opacity)
                draw.line((*pts[i],*pts[i+1]),fill=col,width=max(2,int(vis_w/bars*.3)))
    else:
        step=vis_w/bars
        fraction=max(.08,min(.96,settings.bar_width_pct/100))
        if "dünne" in style: fraction=min(fraction,.22)
        bar_w=max(1,int(step*fraction)); half=total_h/2 if settings.mirrored else total_h
        for i,amp in enumerate(amps):
            bh=max(2,int(half*amp)); bx=int(x0+i*step+(step-bar_w)/2); fill=(*_gradient_color(stops,i/max(1,bars-1)),opacity)
            if "punkte" in style:
                r=max(2,bar_w//2); yy=center_y-bh
                draw.ellipse((bx,yy-r,bx+2*r,yy+r),fill=fill)
                if settings.mirrored: draw.ellipse((bx,center_y+bh-r,bx+2*r,center_y+bh+r),fill=fill)
            elif "dünne" in style:
                draw.line((bx,center_y-bh,bx,center_y+bh if settings.mirrored else center_y),fill=fill,width=max(1,bar_w))
            else:
                radius=max(1,bar_w//2)
                if settings.mirrored:
                    draw.rounded_rectangle((bx,center_y-bh,bx+bar_w,center_y-1),radius=radius,fill=fill)
                    draw.rounded_rectangle((bx,center_y+1,bx+bar_w,center_y+bh),radius=radius,fill=fill)
                else:
                    draw.rounded_rectangle((bx,center_y-bh,bx+bar_w,center_y),radius=radius,fill=fill)
    if settings.glow:
        glow=visual.filter(ImageFilter.GaussianBlur(radius=max(2,int(min(width,height)*.01))))
        alpha=glow.getchannel("A").point(lambda a:int(a*.58)); glow.putalpha(alpha); base=Image.alpha_composite(base,glow)
    base=Image.alpha_composite(base,visual)
    artwork=make_artwork_overlay(width,height,settings,logo_path=logo_path,cover_path=cover_path,badge_override=badge_override,cover_override=cover_override)
    return Image.alpha_composite(base,artwork).convert("RGB")


def build_filtergraph(
    meta: dict,
    settings: RenderSettings,
    has_artwork: bool,
    video_source: str = "[0:v]",
    audio_source: str = "[0:a]",
    glow_sigma: float = 8.0,
) -> tuple[str, dict]:
    width = meta["width"]
    height = meta["height"]
    fps = meta["fps"]
    geom = compute_visualizer_layout(width, height, settings)
    bars = geom["bars"]
    vis_w = geom["width"]
    total_vis_h = geom["height"]
    half_h = geom["half_height"]
    opacity = max(0.05, min(1.0, settings.opacity_pct / 100.0))
    y_center = geom["center_y"]
    x = geom["x"]
    y = geom["y"]

    left_mix = _mix_color_expr(settings.color_left)
    mid_mix = _mix_color_expr(settings.color_mid)
    right_mix = _mix_color_expr(settings.color_right)
    filters: list[str] = []

    # Preserve original audio for output while creating a weighted three-band
    # analysis signal. This makes Bass/Mitten/Höhen controls meaningful without
    # altering the exported soundtrack.
    bass = max(0.0, min(2.0, settings.bass_weight_pct / 100.0))
    mids = max(0.0, min(2.0, settings.mid_weight_pct / 100.0))
    treble = max(0.0, min(2.0, settings.treble_weight_pct / 100.0))
    filters.append(f"{audio_source}asplit=4[aout][al0][am0][ah0]")
    filters.append(f"[al0]lowpass=f=250,volume={bass:.3f}[al]")
    filters.append(f"[am0]highpass=f=250,lowpass=f=4000,volume={mids:.3f}[am]")
    filters.append(f"[ah0]highpass=f=4000,volume={treble:.3f}[ah]")
    filters.append(
        f"[al][am][ah]amix=inputs=3:normalize=0,volume={max(0.1,min(8.0,settings.sensitivity)):.3f},"
        "aformat=channel_layouts=stereo[avis]"
    )

    style = (settings.visualizer_style or "Klassische Balken").strip().lower()
    smooth_avg = max(1, min(20, int(round(1 + settings.smoothness_pct * 19 / 100))))

    def colorize_rect(src: str, out: str, w: int, h: int) -> None:
        third = max(2, w // 3)
        filters.append(f"{src}split=3[g1][g2][g3]")
        filters.append(f"[g1]crop={third}:{h}:0:0,colorchannelmixer={left_mix}[c1]")
        filters.append(f"[g2]crop={third}:{h}:{third}:0,colorchannelmixer={mid_mix}[c2]")
        filters.append(f"[g3]crop={w-2*third}:{h}:{2*third}:0,colorchannelmixer={right_mix}[c3]")
        filters.append(f"[c1][c2][c3]hstack=inputs=3,format=rgba,colorkey=0x000000:0.08:0.02,colorchannelmixer=aa={opacity:.4f}{out}")

    visual_label = "[vis]"
    overlay_x, overlay_y = x, geom["y_centered"]
    vis_out_w, vis_out_h = vis_w, total_vis_h

    if "kreis" in style or "ring" in style:
        if "ring" in style and settings.show_logo:
            badge_size = max(64, int(min(width, height) * settings.logo_size_pct / 100))
            scope_size = max(120, min(min(width, height), badge_size + total_vis_h * 2))
            cx = int(width * max(0,min(100,settings.logo_x_pct))/100)
            cy = int(height * max(0,min(110,settings.logo_y_pct))/100)
        else:
            scope_size = max(140, min(min(width, height), total_vis_h * 3))
            cx = width // 2
            cy = y_center
        if scope_size % 2: scope_size += 1
        midrgb = _hex_rgb(settings.color_mid)
        filters.append(
            f"[avis]avectorscope=s={scope_size}x{scope_size}:rate={fps:.6f}:mode=polar:draw=aaline:scale=sqrt:"
            f"rc=255:gc=255:bc=255:ac=255:rf=10:gf=10:bf=10:af={max(2,min(35, smooth_avg))}:zoom=1.4[scope0]"
        )
        filters.append(
            f"[scope0]format=rgba,colorkey=0x000000:0.12:0.03,colorchannelmixer="
            f"rr={midrgb[0]/255:.5f}:gg={midrgb[1]/255:.5f}:bb={midrgb[2]/255:.5f}:aa={opacity:.4f}[vis]"
        )
        overlay_x = cx - scope_size // 2
        overlay_y = cy - scope_size // 2
        vis_out_w = vis_out_h = scope_size
    elif "waveform" in style or "doppelwelle" in style:
        wave_h = half_h if "doppelwelle" in style else total_vis_h
        filters.append(f"[avis]showwaves=s={vis_w}x{wave_h}:mode=p2p:rate={fps:.6f}:colors=white:scale=sqrt[wave0]")
        colorize_rect("[wave0]", "[wavespec]", vis_w, wave_h)
        if "doppelwelle" in style:
            filters.append("[wavespec]split=2[wt][wb]")
            filters.append("[wb]vflip[wbf]")
            filters.append("[wt][wbf]vstack=inputs=2[vis]")
        else:
            filters.append("[wavespec]copy[vis]")
    else:
        mode = "bar"
        if "dünne" in style:
            mode = "line"
        elif "punkte" in style:
            mode = "dot"
        raw_w = bars if mode == "bar" else vis_w
        raw_h = half_h if settings.mirrored else total_vis_h
        filters.append(
            f"[avis]pan=mono|c0=0.5*c0+0.5*c1,showfreqs=s={raw_w}x{raw_h}:rate={fps:.6f}:mode={mode}:"
            f"ascale=log:fscale=log:win_size=2048:win_func=hann:overlap=0.75:averaging={smooth_avg}:colors=white[rawfreq]"
        )
        if mode == "bar":
            cell_w = max(2, int(round(vis_w / bars)))
            desired_bar_w = max(1, int(round(cell_w * max(8, min(96, settings.bar_width_pct)) / 100.0)))
            gap = max(1, cell_w - desired_bar_w)
            filters.append(f"[rawfreq]scale={vis_w}:{raw_h}:flags=neighbor,drawgrid=w={cell_w}:h={raw_h}:t={gap}:c=black[shape0]")
        else:
            filters.append(f"[rawfreq]scale={vis_w}:{raw_h}:flags=bilinear[shape0]")
            cell_w = max(1, int(round(vis_w / bars)))
            desired_bar_w = 1 if mode == "line" else max(2, cell_w//2)
        colorize_rect("[shape0]", "[spec]", vis_w, raw_h)
        if settings.mirrored:
            filters.append("[spec]split=2[sp1][sp2]")
            filters.append("[sp2]vflip[sp2f]")
            filters.append("[sp1][sp2f]vstack=inputs=2[vis]")
            overlay_y = geom["y_centered"]
        else:
            filters.append("[spec]copy[vis]")
            # showfreqs/showfreqs line/dot output is bottom-anchored when used
            # as a one-sided visualizer. Match the Pillow preview, whose
            # baseline is exactly center_y and whose bars extend upward.
            overlay_y = geom["y_one_sided"]

    if settings.glow:
        filters.append(f"{visual_label}split=2[vglow0][vsharp]")
        filters.append(f"[vglow0]gblur=sigma={max(1.0,float(glow_sigma)):.2f}:steps=1,colorchannelmixer=aa=0.55[vglow]")
        filters.append(f"{video_source}[vglow]overlay=x={overlay_x}:y={overlay_y}:format=auto[tmpglow]")
        filters.append(f"[tmpglow][vsharp]overlay=x={overlay_x}:y={overlay_y}:format=auto[vbase]")
    else:
        filters.append(f"{video_source}{visual_label}overlay=x={overlay_x}:y={overlay_y}:format=auto[vbase]")

    if has_artwork:
        filters.append(f"[1:v]format=rgba,scale={width}:{height}[art]")
        filters.append("[vbase][art]overlay=x=0:y=0:format=auto[vart]")
        filters.append("[vart]format=yuv420p[vout]")
    else:
        filters.append("[vbase]format=yuv420p[vout]")

    return ";".join(filters), {
        "visualizer_width": vis_out_w,
        "visualizer_height": vis_out_h,
        "x": overlay_x,
        "y": overlay_y,
        "style": settings.visualizer_style,
    }


class Renderer:
    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        self.ffmpeg_path = ffmpeg_path or find_binary("ffmpeg")
        self.ffprobe_path = ffprobe_path or find_binary("ffprobe")
        self._process: Optional[subprocess.Popen] = None
        self._preview_process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._preview_lock = threading.Lock()

    def validate(self) -> None:
        if not self.ffmpeg_path or not Path(self.ffmpeg_path).is_file():
            raise VisualizerError(
                "Die integrierte Video-Engine wurde nicht gefunden. "
                "Bitte Audio Visualizer Studio reparieren oder neu installieren."
            )
        if not self.ffprobe_path or not Path(self.ffprobe_path).is_file():
            raise VisualizerError(
                "Die integrierte Analyse-Engine wurde nicht gefunden. "
                "Bitte Audio Visualizer Studio reparieren oder neu installieren."
            )

    def cancel(self) -> None:
        with self._lock:
            if self._process and self._process.poll() is None:
                self._process.terminate()

    def cancel_preview(self) -> None:
        with self._preview_lock:
            if self._preview_process and self._preview_process.poll() is None:
                try:
                    self._preview_process.terminate()
                except Exception:
                    pass

    def _validate_inputs(
        self,
        input_video: str,
        settings: RenderSettings,
        logo_path: Optional[str],
        cover_path: Optional[str],
    ) -> tuple[dict, bool]:
        self.validate()
        if not os.path.isfile(input_video):
            raise VisualizerError("Eingabevideo wurde nicht gefunden.")
        if settings.show_logo and (not logo_path or not os.path.isfile(logo_path)):
            raise VisualizerError("Logo wurde nicht gefunden. Logo deaktivieren oder eine gültige Bilddatei wählen.")
        if settings.show_cover and (not cover_path or not os.path.isfile(cover_path)):
            raise VisualizerError("Cover wurde nicht gefunden. Cover deaktivieren oder eine gültige Bilddatei wählen.")
        meta = probe_media(input_video, self.ffprobe_path)
        has_artwork = bool(
            (settings.show_logo and logo_path)
            or (settings.show_cover and cover_path)
            or (settings.show_text and ((settings.title or "").strip() or (settings.artist or "").strip()))
        )
        return meta, has_artwork

    def extract_preview_frame(
        self,
        input_video: str,
        output_image: str,
        start_seconds: float = 0.0,
        max_width: int = 720,
        media_meta: Optional[dict] = None,
    ) -> None:
        """Extract one small video frame for the static UI preview."""
        self.validate()
        if not os.path.isfile(input_video):
            raise VisualizerError("Eingabevideo wurde nicht gefunden.")
        meta = dict(media_meta) if media_meta else probe_media(input_video, self.ffprobe_path or "")
        start_seconds = max(0.0, min(float(start_seconds), max(0.0, float(meta["duration"]) - 0.05)))
        preview_w = min(max(240, int(max_width)), int(meta["width"]))
        if preview_w % 2:
            preview_w -= 1
        preview_h = int(round(meta["height"] * preview_w / meta["width"]))
        if preview_h % 2:
            preview_h += 1
        Path(output_image).resolve().parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            self.ffmpeg_path or "ffmpeg",
            "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{start_seconds:.3f}",
            "-i", input_video,
            "-frames:v", "1",
            "-vf", f"scale={preview_w}:{preview_h}:flags=bilinear",
            "-q:v", "2",
            output_image,
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creationflags(),
        )
        with self._preview_lock:
            self._preview_process = proc
        try:
            _stdout, stderr = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            _stdout, stderr = proc.communicate()
            raise VisualizerError("Vorschaubild hat zu lange gebraucht und wurde abgebrochen.")
        finally:
            with self._preview_lock:
                if self._preview_process is proc:
                    self._preview_process = None
        if proc.returncode != 0:
            msg = "\n".join((stderr or "").splitlines()[-15:]).strip()
            raise VisualizerError(msg or "Vorschaubild konnte nicht erstellt werden.")
        if not os.path.isfile(output_image) or os.path.getsize(output_image) < 256:
            raise VisualizerError("Vorschaubild wurde nicht korrekt erstellt.")

    def render_preview_gif(
        self,
        input_video: str,
        output_gif: str,
        settings: RenderSettings,
        logo_path: Optional[str] = None,
        start_seconds: float = 0.0,
        preview_duration: float = 1.0,
        preview_fps: int = 6,
        max_width: int = 420,
        media_meta: Optional[dict] = None,
        fast_mode: bool = True,
    ) -> None:
        """Render a short low-resolution animated preview.

        v1.3 deliberately scales the source video *before* the visualizer/glow
        graph. Older versions performed the complete effect at 1080p/4K and
        only scaled the finished result afterwards, which made a UI preview
        unnecessarily expensive.
        """
        self.validate()
        if not os.path.isfile(input_video):
            raise VisualizerError("Eingabevideo wurde nicht gefunden.")
        if settings.show_logo and (not logo_path or not os.path.isfile(logo_path)):
            raise VisualizerError("Logo wurde nicht gefunden.")

        meta = dict(media_meta) if media_meta else probe_media(input_video, self.ffprobe_path or "")
        has_logo = bool(settings.show_logo and logo_path)
        duration = max(0.55, min(float(preview_duration), meta["duration"]))
        max_start = max(0.0, meta["duration"] - duration - 0.05)
        start_seconds = max(0.0, min(float(start_seconds), max_start))

        preview_w = min(max_width, int(meta["width"]))
        if preview_w % 2:
            preview_w -= 1
        preview_w = max(240, preview_w)
        preview_h = int(round(meta["height"] * preview_w / meta["width"]))
        if preview_h % 2:
            preview_h += 1
        pfps = max(4, min(15, int(preview_fps)))

        preview_meta = dict(meta)
        preview_meta.update({"width": preview_w, "height": preview_h, "fps": float(pfps)})

        out_parent = Path(output_gif).resolve().parent
        out_parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="audio_visualizer_preview_") as tmpdir:
            effect_graph, layout = build_filtergraph(
                preview_meta,
                settings,
                has_logo,
                video_source="[previewbase]",
                audio_source="[0:a]",
                glow_sigma=3.0 if fast_mode else 5.0,
            )
            badge_path = None
            if has_logo:
                badge_path = os.path.join(tmpdir, "artwork.png")
                artwork = make_artwork_overlay(
                    preview_w, preview_h, settings, logo_path=logo_path, cover_path=None
                )
                artwork.save(badge_path, "PNG")

            scale_flags = "bilinear" if fast_mode else "lanczos"
            colors = 96 if fast_mode else 160
            preview_graph = (
                f"[0:v]scale={preview_w}:{preview_h}:flags={scale_flags}[previewbase];"
                + effect_graph
                + ";[aout]anullsink"
                + f";[vout]fps={pfps},split=2[p0][p1]"
                + f";[p0]palettegen=max_colors={colors}:stats_mode=diff[pal]"
                + ";[p1][pal]paletteuse=dither=bayer:bayer_scale=4:diff_mode=rectangle[vpreview]"
            )

            cmd = [
                self.ffmpeg_path or "ffmpeg",
                "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_seconds:.3f}",
                "-i", input_video,
            ]
            if badge_path:
                cmd += ["-i", badge_path]
            cmd += [
                "-filter_complex", preview_graph,
                "-map", "[vpreview]",
                "-an",
                "-t", f"{duration:.3f}",
                "-loop", "0",
                output_gif,
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_creationflags(),
            )
            with self._preview_lock:
                self._preview_process = proc
            try:
                _stdout, stderr = proc.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                _stdout, stderr = proc.communicate()
                raise VisualizerError("Live-Vorschau hat zu lange gebraucht und wurde abgebrochen.")
            finally:
                with self._preview_lock:
                    if self._preview_process is proc:
                        self._preview_process = None

            if proc.returncode != 0:
                if proc.returncode in (-15, 1) and not os.path.isfile(output_gif):
                    raise VisualizerError("Vorschau wurde wegen neuer Einstellungen abgebrochen.")
                msg = "\n".join((stderr or "").splitlines()[-20:]).strip()
                raise VisualizerError(msg or "Live-Vorschau konnte nicht erstellt werden.")
            if not os.path.isfile(output_gif) or os.path.getsize(output_gif) < 512:
                raise VisualizerError("Live-Vorschau wurde nicht korrekt erstellt.")

    def render(
        self,
        input_video: str,
        output_video: str,
        settings: RenderSettings,
        logo_path: Optional[str] = None,
        cover_path: Optional[str] = None,
        progress_cb: Optional[Callable[[float, str], None]] = None,
    ) -> None:
        meta, has_artwork = self._validate_inputs(input_video, settings, logo_path, cover_path)

        output_parent = Path(output_video).resolve().parent
        output_parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="audio_visualizer_") as tmpdir:
            artwork_path = None
            filtergraph, _layout = build_filtergraph(meta, settings, has_artwork)
            if has_artwork:
                artwork_path = os.path.join(tmpdir, "artwork.png")
                design_w, design_h = design_reference_size(meta["width"], meta["height"])
                artwork = make_artwork_overlay(
                    design_w, design_h, settings,
                    logo_path=logo_path, cover_path=cover_path,
                )
                artwork.save(artwork_path, "PNG")

            cmd = [self.ffmpeg_path or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", input_video]
            if artwork_path:
                cmd += ["-loop", "1", "-i", artwork_path]
            cmd += [
                "-filter_complex", filtergraph,
                "-map", "[vout]",
                "-map", "[aout]",
                "-c:v", "libx264",
                "-preset", settings.video_preset,
                "-crf", str(max(14, min(30, int(settings.crf)))),
                "-c:a", "aac",
                "-b:a", "192k",
                "-movflags", "+faststart",
                "-t", f"{meta['duration']:.6f}",
                "-progress", "pipe:1",
                "-nostats",
                output_video,
            ]

            if progress_cb:
                progress_cb(0.0, "Rendering gestartet …")

            with self._lock:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=_creationflags(),
                )

            assert self._process.stdout is not None
            for line in self._process.stdout:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                if key in ("out_time_us", "out_time_ms"):
                    try:
                        seconds = float(value) / 1_000_000.0
                        pct = min(0.995, max(0.0, seconds / meta["duration"]))
                        if progress_cb:
                            progress_cb(pct, f"Rendering … {pct * 100:.0f} %")
                    except ValueError:
                        pass
                elif key == "progress" and value == "end":
                    if progress_cb:
                        progress_cb(1.0, "Fertig")

            assert self._process.stderr is not None
            stderr_lines = self._process.stderr.read().splitlines()
            returncode = self._process.wait()
            with self._lock:
                self._process = None

            if returncode != 0:
                message = "\n".join(stderr_lines[-20:]).strip()
                if returncode < 0:
                    raise VisualizerError("Rendering wurde abgebrochen.")
                raise VisualizerError(message or f"FFmpeg wurde mit Fehlercode {returncode} beendet.")

            if not os.path.isfile(output_video) or os.path.getsize(output_video) < 1024:
                raise VisualizerError("Ausgabedatei wurde nicht korrekt erstellt.")
