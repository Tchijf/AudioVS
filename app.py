from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from visualizer_engine import (
    RenderSettings,
    Renderer,
    make_cover_image,
    make_logo_badge_image,
    make_static_preview,
    measure_artwork_layout,
    compute_visualizer_layout,
    design_reference_size,
    probe_media,
)

APP_TITLE = "Audio Visualizer Studio v2.1"
HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

PALETTES = {
    "dark": {
        "BG": "#080d18",
        "SURFACE": "#111827",
        "SURFACE_2": "#192235",
        "PANEL": "#050914",
        "ACCENT": "#7c5cff",
        "ACCENT_2": "#21c7d9",
        "TEXT": "#f7f9ff",
        "TEXT_MUTED": "#aebbd2",
        "BORDER": "#2d3a52",
        "INPUT_BG": "#0c1424",
        "ACTIVE": "#26334a",
        "ACCENT_ACTIVE": "#6847ee",
        "SUCCESS": "#35d07f",
        "DANGER": "#ff6b78",
    },
    "light": {
        "BG": "#f2f5f9",
        "SURFACE": "#ffffff",
        "SURFACE_2": "#e9eef6",
        "PANEL": "#dfe6ef",
        "ACCENT": "#5b43d6",
        "ACCENT_2": "#087f96",
        "TEXT": "#111827",
        "TEXT_MUTED": "#42526a",
        "BORDER": "#b7c2d0",
        "INPUT_BG": "#ffffff",
        "ACTIVE": "#dbe4f0",
        "ACCENT_ACTIVE": "#4933bd",
        "SUCCESS": "#16814d",
        "DANGER": "#bd2738",
    },
}


def _use_palette(mode: str) -> None:
    palette = PALETTES.get(mode, PALETTES["dark"])
    globals().update(palette)


_use_palette("dark")


def resource_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).resolve().parent


class VisualizerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        # Start with a safe fallback size. The final Windows work-area/maximized
        # size is applied only after the UI has been mapped. Applying ``zoomed``
        # too early can be ignored by Tk on some Windows/display-scaling setups.
        self.geometry("1280x820+0+0")
        self.minsize(1080, 700)
        self.app_settings = self._read_app_settings()
        self.theme_mode = str(self.app_settings.get("theme", "dark")).lower()
        if self.theme_mode not in ("dark", "light"):
            self.theme_mode = "dark"
        _use_palette(self.theme_mode)
        self.configure(bg=BG)
        self._tk_theme_widgets: list[tuple[tk.Widget, str]] = []
        self._configure_style()
        self._set_window_icon()

        self.renderer = Renderer()
        self.render_thread: threading.Thread | None = None
        self.frame_thread: threading.Thread | None = None
        self.frame_after_id: str | None = None
        self.redraw_after_id: str | None = None
        self.frame_request_id = 0
        self.media_meta: dict | None = None
        self.preview_temp = tempfile.TemporaryDirectory(prefix="avs_preview_ui_")
        self.preview_base_image: Image.Image | None = None
        self.preview_design_base_image: Image.Image | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.cover_thumb_photo: ImageTk.PhotoImage | None = None
        self.badge_cache_key: tuple | None = None
        self.badge_cache_image: Image.Image | None = None
        self.cover_cache_key: tuple | None = None
        self.cover_cache_image: Image.Image | None = None
        self.drag_state: dict | None = None
        self.eyedropper_target: tk.StringVar | None = None
        self.preview_composed_image: Image.Image | None = None
        self.preview_display_image: Image.Image | None = None
        self.preview_display_scale = 1.0
        self.preview_display_offset = (0, 0)
        self._preview_resize_after_id: str | None = None
        self.active_preset_name: str | None = None
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self._history_suspend = True
        self._history_last_snapshot: dict | None = None
        self._history_after_id: str | None = None
        self._autosave_after_id: str | None = None
        self._session_was_unclean = self._session_lock_file().exists()
        try:
            self._session_lock_file().write_text("running", encoding="utf-8")
        except Exception:
            pass

        self.default_logo = resource_dir() / "assets" / "default_logo.png"
        self.app_icon_png = resource_dir() / "assets" / "app_icon.png"
        self.app_icon_ico = resource_dir() / "assets" / "app_icon.ico"
        self.color_buttons: dict[str, tk.Button] = {}
        self.header_logo_photo: ImageTk.PhotoImage | None = None
        self.header_logo_label: tk.Label | None = None
        self.header_pill: tk.Label | None = None

        self._build_vars()
        self._build_ui()
        # Apply the desired standard size after the window exists on screen.
        # A second pass handles Windows DPI/display-scaling race conditions.
        self.after_idle(self._fit_window_to_screen)
        self.after(180, self._fit_window_to_screen)
        self._load_presets_into_ui()
        self._bind_preview_updates()
        self._update_color_swatches()
        self._update_ffmpeg_status()
        self._apply_theme_to_raw_widgets()
        self._theme_initialized = True
        self._history_last_snapshot = self._snapshot_state()
        self._history_suspend = False
        self.bind_all("<Control-z>", self.undo)
        self.bind_all("<Control-y>", self.redo)
        self.bind_all("<Control-Z>", self.undo)
        self.bind_all("<Control-Y>", self.redo)
        self.after(450, self._ensure_ffmpeg_if_missing)
        self.after(700, self._offer_autosave_recovery)
        self._schedule_autosave()
        self.protocol("WM_DELETE_WINDOW", self._on_close)


    def _fit_window_to_screen(self):
        """Open in the standard large workspace shown in the reference image.

        On Windows this means a normal *maximized* application window (not
        borderless fullscreen), so the title bar stays visible and the taskbar
        is respected. This gives the left controls and the complete preview the
        maximum usable space immediately after startup.
        """
        try:
            self.update_idletasks()
        except tk.TclError:
            return

        if sys.platform.startswith("win"):
            try:
                self.attributes("-fullscreen", False)
            except tk.TclError:
                pass
            try:
                # ``zoomed`` is the Windows maximize state used in the user's
                # reference screenshot. Keep normal title bar/window controls.
                self.wm_state("zoomed")
                return
            except tk.TclError:
                try:
                    self.state("zoomed")
                    return
                except tk.TclError:
                    pass

            # Fallback for unusual Tk/Windows combinations: use the Windows
            # desktop work area (excludes taskbar) instead of raw screen size.
            try:
                import ctypes
                from ctypes import wintypes

                rect = wintypes.RECT()
                SPI_GETWORKAREA = 48
                if ctypes.windll.user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(rect), 0):
                    work_w = max(900, rect.right - rect.left)
                    work_h = max(620, rect.bottom - rect.top)
                    self.geometry(f"{work_w}x{work_h}+{rect.left}+{rect.top}")
                    return
            except Exception:
                pass

        screen_w = max(800, self.winfo_screenwidth())
        screen_h = max(600, self.winfo_screenheight())
        win_w = max(900, screen_w - 24)
        win_h = max(620, screen_h - 72)
        try:
            self.geometry(f"{win_w}x{win_h}+0+0")
        except tk.TclError:
            pass


    def _configure_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.option_add("*Font", "{Segoe UI} 10")
        self.option_add("*TCombobox*Listbox.background", INPUT_BG)
        self.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        self.option_add("*TCombobox*Listbox.selectForeground", TEXT)

        style.configure("App.TFrame", background=BG)
        style.configure("TFrame", background=SURFACE)
        style.configure("Card.TFrame", background=SURFACE)
        style.configure("Section.TLabelframe", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1, padding=2)
        style.configure("Section.TLabelframe.Label", background=SURFACE, foreground=TEXT, font=("Segoe UI Semibold", 11))
        style.configure("TLabel", background=SURFACE, foreground=TEXT)
        style.configure("Muted.TLabel", background=BG, foreground=TEXT_MUTED)
        style.configure("Section.TLabel", background=SURFACE, foreground=TEXT)
        style.configure("HeroTitle.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 24))
        style.configure("HeroSub.TLabel", background=BG, foreground=TEXT_MUTED, font=("Segoe UI", 10))
        style.configure("Stat.TLabel", background=SURFACE_2, foreground=TEXT_MUTED, font=("Segoe UI", 9))

        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", font=("Segoe UI Semibold", 10), padding=(16, 10), background=SURFACE_2, foreground=TEXT_MUTED, borderwidth=0)
        style.map("TNotebook.Tab", background=[("selected", ACCENT), ("active", ACTIVE)], foreground=[("selected", "#ffffff"), ("active", TEXT)])

        style.configure("TButton", background=SURFACE_2, foreground=TEXT, bordercolor=BORDER, focusthickness=1, focuscolor=ACCENT, padding=(11, 7), font=("Segoe UI Semibold", 9))
        style.map("TButton", background=[("active", ACTIVE)])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", bordercolor=ACCENT, padding=(13, 8), font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", ACCENT_ACTIVE)])
        style.configure("Secondary.TButton", background=SURFACE_2, foreground=TEXT, bordercolor=BORDER, padding=(10, 6))

        style.configure("TCheckbutton", background=SURFACE, foreground=TEXT)
        style.map("TCheckbutton", background=[("active", SURFACE)], foreground=[("active", TEXT)])
        style.configure("TRadiobutton", background=SURFACE, foreground=TEXT)

        style.configure("TEntry", fieldbackground=INPUT_BG, foreground=TEXT, bordercolor=BORDER, insertcolor=TEXT)
        style.configure("TCombobox", fieldbackground=INPUT_BG, background=INPUT_BG, foreground=TEXT, bordercolor=BORDER, arrowsize=14)
        style.map("TCombobox", fieldbackground=[("readonly", INPUT_BG), ("disabled", SURFACE_2)], foreground=[("readonly", TEXT), ("disabled", TEXT_MUTED)], selectbackground=[("readonly", INPUT_BG)])
        style.map("TEntry", fieldbackground=[("disabled", SURFACE_2)], foreground=[("disabled", TEXT_MUTED)])
        style.map("TCheckbutton", foreground=[("disabled", TEXT_MUTED)])

        style.configure("Horizontal.TProgressbar", troughcolor=INPUT_BG, bordercolor=BORDER, background=ACCENT_2, lightcolor=ACCENT_2, darkcolor=ACCENT_2)
        style.configure("Ready.TLabel", background=SURFACE, foreground=SUCCESS, font=("Segoe UI Semibold", 10))
        style.configure("Error.TLabel", background=SURFACE, foreground=DANGER, font=("Segoe UI Semibold", 10))

    def _change_theme(self, *_args):
        requested = self.theme_mode_var.get().strip().lower() if hasattr(self, "theme_mode_var") else "dunkel"
        mode = "light" if requested.startswith("hell") else "dark"
        if mode == self.theme_mode and getattr(self, "_theme_initialized", False):
            return
        self.theme_mode = mode
        _use_palette(mode)
        self.configure(bg=BG)
        self._configure_style()
        self._apply_theme_to_raw_widgets()
        self.app_settings["theme"] = mode
        self._write_app_settings()
        self._theme_initialized = True

    def _register_theme_widget(self, widget: tk.Widget, role: str) -> None:
        self._tk_theme_widgets.append((widget, role))

    def _apply_theme_to_raw_widgets(self):
        for widget, role in list(self._tk_theme_widgets):
            try:
                if not widget.winfo_exists():
                    continue
                if role == "header_logo":
                    widget.configure(bg=BG, fg=TEXT_MUTED)
                elif role == "pill":
                    widget.configure(bg=SURFACE_2, fg=TEXT, highlightbackground=BORDER)
                elif role == "preview":
                    widget.configure(bg=PANEL, fg=TEXT_MUTED, highlightbackground=BORDER)
                elif role == "scale":
                    widget.configure(bg=SURFACE, fg=TEXT_MUTED, troughcolor=INPUT_BG, activebackground=ACCENT, highlightbackground=SURFACE)
                elif role == "preview_scale":
                    widget.configure(bg=SURFACE, fg=TEXT_MUTED, troughcolor=INPUT_BG, activebackground=ACCENT, highlightbackground=SURFACE)
                elif role == "spin":
                    widget.configure(bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, buttonbackground=SURFACE_2, highlightbackground=BORDER)
                elif role == "entry":
                    widget.configure(bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, highlightbackground=BORDER)
            except Exception:
                pass

    def _set_window_icon(self):
        try:
            if (resource_dir() / "assets" / "app_icon.png").is_file():
                icon = Image.open(resource_dir() / "assets" / "app_icon.png")
                self._window_icon_photo = ImageTk.PhotoImage(icon)
                self.iconphoto(True, self._window_icon_photo)
        except Exception:
            pass
        if os.name == "nt":
            try:
                ico = resource_dir() / "assets" / "app_icon.ico"
                if ico.is_file():
                    self.iconbitmap(default=str(ico))
            except Exception:
                pass

    def _header_logo_path(self) -> Path:
        custom = str(self.app_settings.get("header_logo", "")).strip() if isinstance(self.app_settings, dict) else ""
        if custom and Path(custom).is_file():
            return Path(custom)
        return resource_dir() / "assets" / "app_icon.png"

    def _make_header_logo(self):
        try:
            path = self._header_logo_path()
            if not path.is_file():
                return None
            img = Image.open(path).convert("RGBA")
            img.thumbnail((64, 64), Image.Resampling.LANCZOS)
            canvas = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            canvas.alpha_composite(img, ((64 - img.width) // 2, (64 - img.height) // 2))
            self.header_logo_photo = ImageTk.PhotoImage(canvas)
            return self.header_logo_photo
        except Exception:
            return None

    def choose_header_logo(self):
        path = filedialog.askopenfilename(
            title="Logo links oben auswählen",
            filetypes=[("Bilder", "*.png *.jpg *.jpeg *.webp"), ("Alle Dateien", "*.*")],
        )
        if not path:
            return
        try:
            branding_dir = self._app_data_dir() / "branding"
            branding_dir.mkdir(parents=True, exist_ok=True)
            suffix = Path(path).suffix.lower() or ".png"
            target = branding_dir / f"header_logo{suffix}"
            source = Path(path).resolve()
            if source != target.resolve():
                for old in branding_dir.glob("header_logo.*"):
                    try:
                        old.unlink()
                    except Exception:
                        pass
                shutil.copy2(source, target)
            elif not target.is_file():
                shutil.copy2(source, target)
            self.app_settings["header_logo"] = str(target)
            self._write_app_settings()
            self._refresh_header_logo()
            self.status_var.set("Logo links oben aktualisiert")
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Logo konnte nicht gespeichert werden: {exc}")

    def reset_header_logo(self):
        saved = str(self.app_settings.pop("header_logo", "")).strip()
        if saved:
            try:
                saved_path = Path(saved).resolve()
                branding_dir = (self._app_data_dir() / "branding").resolve()
                if saved_path.is_file() and branding_dir in saved_path.parents:
                    saved_path.unlink(missing_ok=True)
            except Exception:
                pass
        self._write_app_settings()
        self._refresh_header_logo()
        self.status_var.set("Standard-App-Logo wiederhergestellt")

    def _refresh_header_logo(self):
        if self.header_logo_label is None:
            return
        badge = self._make_header_logo()
        if badge is not None:
            self.header_logo_label.configure(image=badge, text="")
        else:
            self.header_logo_label.configure(image="", text="Logo")

    def _build_vars(self):
        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.logo_var = tk.StringVar(value=str(self.default_logo) if self.default_logo.exists() else "")
        self.cover_var = tk.StringVar()

        self.ffmpeg_status_var = tk.StringVar(value="Video-Engine wird geprüft …")
        self.media_info_var = tk.StringVar(value="Noch kein Video gewählt")
        self.status_var = tk.StringVar(value="Bereit")
        self.preview_status_var = tk.StringVar(
            value="Video auswählen. Danach werden Änderungen sofort als statische Vorschau angezeigt."
        )
        self.preview_time_text_var = tk.StringVar(value="00:00.0")

        self.bars_var = tk.IntVar(value=60)
        self.width_var = tk.IntVar(value=82)
        self.bar_width_var = tk.IntVar(value=72)
        self.height_var = tk.IntVar(value=24)
        self.position_var = tk.IntVar(value=50)
        self.opacity_var = tk.IntVar(value=92)
        self.sensitivity_var = tk.DoubleVar(value=1.8)
        self.visualizer_style_var = tk.StringVar(value="Klassische Balken")
        self.bass_var = tk.IntVar(value=100)
        self.mid_var = tk.IntVar(value=100)
        self.treble_var = tk.IntVar(value=100)
        self.smoothness_var = tk.IntVar(value=35)
        self.mirrored_var = tk.BooleanVar(value=True)
        self.glow_var = tk.BooleanVar(value=True)

        self.show_logo_var = tk.BooleanVar(value=True)
        self.logo_size_var = tk.IntVar(value=24)
        self.logo_content_scale_var = tk.IntVar(value=100)
        self.logo_x_var = tk.IntVar(value=50)
        self.logo_y_var = tk.IntVar(value=50)
        self.remove_black_var = tk.BooleanVar(value=True)

        self.show_cover_var = tk.BooleanVar(value=False)
        self.cover_size_var = tk.IntVar(value=22)
        self.cover_x_var = tk.IntVar(value=50)
        self.cover_y_var = tk.IntVar(value=27)
        self.cover_effect_var = tk.StringVar(value="Schatten")
        self.cover_corner_var = tk.IntVar(value=6)
        self.cover_shadow_strength_var = tk.IntVar(value=55)
        self.cover_shadow_angle_var = tk.IntVar(value=135)
        self.cover_shadow_distance_var = tk.IntVar(value=5)
        self.cover_frame_width_var = tk.IntVar(value=0)
        self.cover_frame_color_var = tk.StringVar(value="#ffffff")
        self.cover_glow_strength_var = tk.IntVar(value=0)
        self.cover_glow_color_var = tk.StringVar(value="#a855f7")
        self.glass_enabled_var = tk.BooleanVar(value=False)
        self.glass_opacity_var = tk.IntVar(value=28)

        self.show_text_var = tk.BooleanVar(value=True)
        self.artist_var = tk.StringVar()
        self.title_var = tk.StringVar()
        self.font_name_var = tk.StringVar(value="Segoe UI")
        self.title_size_var = tk.IntVar(value=5)
        self.artist_size_var = tk.IntVar(value=3)
        self.title_x_var = tk.IntVar(value=50)
        self.title_y_var = tk.IntVar(value=82)
        self.artist_x_var = tk.IntVar(value=50)
        self.artist_y_var = tk.IntVar(value=88)
        self.title_bold_var = tk.BooleanVar(value=True)
        self.artist_bold_var = tk.BooleanVar(value=False)
        self.title_uppercase_var = tk.BooleanVar(value=False)
        self.artist_uppercase_var = tk.BooleanVar(value=False)
        self.title_spacing_var = tk.IntVar(value=0)
        self.artist_spacing_var = tk.IntVar(value=0)
        self.text_shadow_var = tk.IntVar(value=55)
        self.text_glow_var = tk.IntVar(value=0)
        # Legacy aliases retained for loading older presets/projects.
        self.text_size_var = tk.IntVar(value=4)
        self.text_x_var = tk.IntVar(value=50)
        self.text_y_var = tk.IntVar(value=86)

        self.color_left_var = tk.StringVar(value="#ff2bd6")
        self.color_mid_var = tk.StringVar(value="#a855f7")
        self.color_right_var = tk.StringVar(value="#00e5ff")
        self.title_color_var = tk.StringVar(value="#ffffff")
        self.artist_color_var = tk.StringVar(value="#d8d8ff")

        self.quality_var = tk.StringVar(value="Hohe Qualität")
        self.progress_var = tk.DoubleVar(value=0)
        self.preview_time_var = tk.DoubleVar(value=0.0)
        self.drag_target_var = tk.StringVar(value="Automatisch")
        self.preset_var = tk.StringVar(value="")
        self.theme_mode_var = tk.StringVar(value="Dunkel" if self.theme_mode == "dark" else "Hell")

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        root = ttk.Frame(self, padding=16, style="App.TFrame")
        root.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root, style="App.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))

        brand_box = ttk.Frame(header, style="App.TFrame")
        brand_box.pack(side="left", padx=(0, 12))
        badge = self._make_header_logo()
        self.header_logo_label = tk.Label(
            brand_box,
            image=badge if badge is not None else "",
            text="" if badge is not None else "Logo",
            bg=BG,
            fg=TEXT_MUTED,
            bd=0,
            cursor="hand2",
        )
        self.header_logo_label.pack(anchor="center")
        self.header_logo_label.bind("<Button-1>", lambda _e: self.choose_header_logo())
        self._register_theme_widget(self.header_logo_label, "header_logo")
        logo_actions = ttk.Frame(brand_box, style="App.TFrame")
        logo_actions.pack(anchor="center", pady=(3, 0))
        ttk.Button(logo_actions, text="Logo ändern", command=self.choose_header_logo, style="Secondary.TButton").pack(side="left")
        ttk.Button(logo_actions, text="Standard", command=self.reset_header_logo, style="Secondary.TButton").pack(side="left", padx=(4, 0))

        title_box = ttk.Frame(header, style="App.TFrame")
        title_box.pack(side="left", fill="x", expand=True)
        ttk.Label(title_box, text="Audio Visualizer Studio", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            title_box,
            text="v2.1 · Installierbare Desktop-Version mit eigenem Setup",
            style="HeroSub.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        header_right = ttk.Frame(header, style="App.TFrame")
        header_right.pack(side="right", anchor="ne")
        theme_row = ttk.Frame(header_right, style="App.TFrame")
        theme_row.pack(anchor="e", pady=(0, 7))
        ttk.Label(theme_row, text="Darstellung", style="HeroSub.TLabel").pack(side="left", padx=(0, 7))
        self.theme_combo = ttk.Combobox(
            theme_row,
            textvariable=self.theme_mode_var,
            values=["Dunkel", "Hell"],
            state="readonly",
            width=10,
        )
        self.theme_combo.pack(side="left")
        self.theme_combo.bind("<<ComboboxSelected>>", self._change_theme)

        self.header_pill = tk.Label(
            header_right,
            text="MP4 · Live-Workflow · Statische Vorschau",
            bg=SURFACE_2,
            fg=TEXT,
            padx=12,
            pady=8,
            font=("Segoe UI", 9, "bold"),
            bd=1,
            relief="solid",
            highlightthickness=1,
            highlightbackground=BORDER,
        )
        self.header_pill.pack(anchor="e")
        self._register_theme_widget(self.header_pill, "pill")

        left = ttk.Frame(root, width=690, style="App.TFrame")
        left.grid(row=1, column=0, sticky="nsw", padx=(0, 14))
        left.columnconfigure(0, weight=1)

        right = ttk.Frame(root, style="App.TFrame")
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)

        files = ttk.LabelFrame(left, text="1. Dateien", padding=12, style="Section.TLabelframe")
        files.grid(row=0, column=0, sticky="ew")
        files.columnconfigure(1, weight=1)

        ttk.Label(files, text="Video", width=10).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(files, textvariable=self.input_var, width=54).grid(row=0, column=1, sticky="ew")
        ttk.Button(files, text="Auswählen …", command=self.choose_video).grid(row=0, column=2, padx=(8, 0))

        ttk.Label(files, text="Ausgabe", width=10).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(7, 0))
        ttk.Entry(files, textvariable=self.output_var).grid(row=1, column=1, sticky="ew", pady=(7, 0))
        ttk.Button(files, text="Speichern als …", command=self.choose_output).grid(row=1, column=2, padx=(8, 0), pady=(7, 0))
        ttk.Label(files, textvariable=self.media_info_var).grid(row=2, column=1, columnspan=2, sticky="w", pady=(7, 0))

        options = ttk.LabelFrame(left, text="2. Gestaltung", padding=10, style="Section.TLabelframe")
        options.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        options.columnconfigure(0, weight=1)

        presetbar = ttk.Frame(options, style="Card.TFrame")
        presetbar.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        presetbar.columnconfigure(1, weight=1)
        ttk.Label(presetbar, text="Preset", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.preset_combo = ttk.Combobox(presetbar, textvariable=self.preset_var, state="readonly", width=26)
        self.preset_combo.grid(row=0, column=1, sticky="ew")
        ttk.Button(presetbar, text="Laden", command=self.load_preset, style="Secondary.TButton").grid(row=0, column=2, padx=(8, 0))
        ttk.Button(presetbar, text="Speichern", command=self.save_preset, style="Accent.TButton").grid(row=0, column=3, padx=(8, 0))
        ttk.Button(presetbar, text="Löschen", command=self.delete_preset, style="Secondary.TButton").grid(row=0, column=4, padx=(8, 0))
        ttk.Button(presetbar, text="↶", width=3, command=self.undo, style="Secondary.TButton").grid(row=0, column=5, padx=(10, 0))
        ttk.Button(presetbar, text="↷", width=3, command=self.redo, style="Secondary.TButton").grid(row=0, column=6, padx=(4, 0))

        notebook = ttk.Notebook(options)
        notebook.grid(row=1, column=0, sticky="ew")

        vis_tab = ttk.Frame(notebook, padding=9)
        asset_tab = ttk.Frame(notebook, padding=9)
        text_tab = ttk.Frame(notebook, padding=9)
        notebook.add(vis_tab, text="Visualizer")
        notebook.add(asset_tab, text="Logo & Cover")
        notebook.add(text_tab, text="Künstler & Titel")

        for c in range(4):
            vis_tab.columnconfigure(c, weight=1)
            asset_tab.columnconfigure(c, weight=1)
            text_tab.columnconfigure(c, weight=1)

        style_row = ttk.Frame(vis_tab, style="Card.TFrame")
        style_row.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 7))
        style_row.columnconfigure(1, weight=1)
        ttk.Label(style_row, text="Visualizer-Design", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(
            style_row,
            textvariable=self.visualizer_style_var,
            values=["Klassische Balken", "Dünne Linien", "Punkte", "Waveform", "Kreis-Visualizer", "Ring um Logo", "Symmetrische Doppelwelle"],
            state="readonly",
        ).grid(row=0, column=1, columnspan=3, sticky="ew")

        self._num_control(vis_tab, "Balken / Details", self.bars_var, 24, 100, 1, 0, increment=1)
        self._num_control(vis_tab, "Visualizer-Breite (%)", self.width_var, 35, 98, 1, 2, increment=1)
        self._num_control(vis_tab, "Balkenbreite (%)", self.bar_width_var, 8, 95, 3, 0, increment=1)
        self._num_control(vis_tab, "Höhe (%)", self.height_var, 8, 55, 3, 2, increment=1)
        self._num_control(vis_tab, "Position vertikal (%)", self.position_var, 0, 110, 5, 0, increment=1)
        self._num_control(vis_tab, "Deckkraft (%)", self.opacity_var, 20, 100, 5, 2, increment=1)
        self._num_control(vis_tab, "Gesamt-Reaktion", self.sensitivity_var, 0.5, 4.0, 7, 0, increment=0.1)
        self._num_control(vis_tab, "Weichheit (%)", self.smoothness_var, 0, 100, 7, 2, increment=1)

        audio_box = ttk.LabelFrame(vis_tab, text="Audio-Reaktion", padding=8, style="Section.TLabelframe")
        audio_box.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(6, 0))
        for c in range(4): audio_box.columnconfigure(c, weight=1)
        self._num_control(audio_box, "Bass (%)", self.bass_var, 0, 200, 0, 0, increment=5)
        self._num_control(audio_box, "Mitten (%)", self.mid_var, 0, 200, 0, 2, increment=5)
        self._num_control(audio_box, "Höhen (%)", self.treble_var, 0, 200, 2, 0, increment=5)
        opts = ttk.Frame(audio_box, style="Card.TFrame")
        opts.grid(row=2, column=2, columnspan=2, sticky="w", padx=(10, 0), pady=(4, 0))
        ttk.Checkbutton(opts, text="Spiegeln", variable=self.mirrored_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(opts, text="Glow", variable=self.glow_var).pack(side="left")

        colors = ttk.LabelFrame(vis_tab, text="Visualizer-Farben", padding=8, style="Section.TLabelframe")
        colors.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Label(colors, text="Farbfeld, Hex-Code oder Pipette aus dem Vorschaubild:").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 5))
        self._color_row(colors, [("Links", "left", self.color_left_var), ("Mitte", "mid", self.color_mid_var), ("Rechts", "right", self.color_right_var)], row_start=1)

        quality = ttk.Frame(vis_tab, style="Card.TFrame")
        quality.grid(row=12, column=0, columnspan=4, sticky="w", pady=(9, 0))
        ttk.Label(quality, text="Exportqualität", style="Section.TLabel").pack(side="left")
        ttk.Combobox(quality, textvariable=self.quality_var, values=["Sehr hohe Qualität", "Hohe Qualität", "Schneller Export"], state="readonly", width=19).pack(side="left", padx=(8, 0))

        logo_box = ttk.LabelFrame(asset_tab, text="Logo", padding=10, style="Section.TLabelframe")
        logo_box.grid(row=0, column=0, columnspan=4, sticky="ew")
        logo_box.columnconfigure(1, weight=1)
        ttk.Checkbutton(logo_box, text="Logo anzeigen", variable=self.show_logo_var).grid(row=0, column=0, sticky="w")
        ttk.Entry(logo_box, textvariable=self.logo_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(logo_box, text="Logo wählen …", command=self.choose_logo).grid(row=0, column=2)
        ttk.Checkbutton(logo_box, text="Schwarz entfernen", variable=self.remove_black_var).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._num_control(logo_box, "Kreis/Badge-Größe (%)", self.logo_size_var, 8, 55, 2, 0, increment=1)
        self._num_control(logo_box, "Logo X (%)", self.logo_x_var, 0, 100, 2, 2, increment=1)
        self._num_control(logo_box, "Importiertes Logo (%)", self.logo_content_scale_var, 20, 180, 4, 0, increment=1)
        self._num_control(logo_box, "Logo Y (%)", self.logo_y_var, 0, 110, 4, 2, increment=1)

        cover_box = ttk.LabelFrame(asset_tab, text="Cover", padding=10, style="Section.TLabelframe")
        cover_box.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        cover_box.columnconfigure(1, weight=1)
        ttk.Checkbutton(cover_box, text="Cover anzeigen", variable=self.show_cover_var).grid(row=0, column=0, sticky="w")
        ttk.Entry(cover_box, textvariable=self.cover_var).grid(row=0, column=1, sticky="ew", padx=(8, 8))
        ttk.Button(cover_box, text="Cover wählen …", command=self.choose_cover).grid(row=0, column=2)
        self._num_control(cover_box, "Cover-Größe (%)", self.cover_size_var, 8, 60, 2, 0, increment=1)
        self._num_control(cover_box, "Cover X (%)", self.cover_x_var, 0, 100, 2, 2, increment=1)
        self._num_control(cover_box, "Cover Y (%)", self.cover_y_var, 0, 110, 4, 0, increment=1)
        self.cover_thumb_label = ttk.Label(cover_box, text="Keine Cover-Vorschau", anchor="center")
        self.cover_thumb_label.grid(row=4, column=2, padx=(12, 0), pady=(6, 0), sticky="nsew")

        cover_style = ttk.LabelFrame(asset_tab, text="Cover-Styling", padding=8, style="Section.TLabelframe")
        cover_style.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for c in range(4): cover_style.columnconfigure(c, weight=1)
        self._num_control(cover_style, "Runde Ecken (%)", self.cover_corner_var, 0, 50, 0, 0, increment=1)
        self._num_control(cover_style, "Schattenstärke (%)", self.cover_shadow_strength_var, 0, 100, 0, 2, increment=1)
        self._num_control(cover_style, "Schattenwinkel (°)", self.cover_shadow_angle_var, 0, 359, 2, 0, increment=5)
        self._num_control(cover_style, "Schattenabstand (%)", self.cover_shadow_distance_var, 0, 25, 2, 2, increment=1)
        self._num_control(cover_style, "Rahmenbreite (%)", self.cover_frame_width_var, 0, 12, 4, 0, increment=1)
        self._num_control(cover_style, "Glow-Stärke (%)", self.cover_glow_strength_var, 0, 100, 4, 2, increment=1)
        cover_colors = ttk.Frame(cover_style, style="Card.TFrame")
        cover_colors.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(4, 0))
        self._color_row(cover_colors, [("Rahmen", "cover_frame", self.cover_frame_color_var), ("Glow", "cover_glow", self.cover_glow_color_var)], row_start=0)
        glass_row = ttk.Frame(cover_style, style="Card.TFrame")
        glass_row.grid(row=9, column=0, columnspan=4, sticky="ew", pady=(5, 0))
        ttk.Checkbutton(glass_row, text="Glass-Hintergrund hinter Cover & Text", variable=self.glass_enabled_var).pack(side="left")
        ttk.Label(glass_row, text="Deckkraft:", style="Section.TLabel").pack(side="left", padx=(14, 4))
        tk.Spinbox(glass_row, textvariable=self.glass_opacity_var, from_=5, to=70, width=6, bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, buttonbackground=SURFACE_2, relief="flat").pack(side="left")

        ttk.Checkbutton(text_tab, text="Künstler / Titel anzeigen", variable=self.show_text_var).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 7))
        font_row = ttk.Frame(text_tab, style="Card.TFrame")
        font_row.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(0, 7))
        font_row.columnconfigure(1, weight=1)
        ttk.Label(font_row, text="Schriftart", style="Section.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(font_row, textvariable=self.font_name_var, values=["Segoe UI", "Arial", "Calibri", "Verdana", "Georgia", "Times New Roman", "Impact", "Trebuchet MS"], state="readonly").grid(row=0, column=1, sticky="ew")

        title_box = ttk.LabelFrame(text_tab, text="Titel", padding=8, style="Section.TLabelframe")
        title_box.grid(row=2, column=0, columnspan=2, sticky="nsew", padx=(0, 5))
        artist_box = ttk.LabelFrame(text_tab, text="Künstler", padding=8, style="Section.TLabelframe")
        artist_box.grid(row=2, column=2, columnspan=2, sticky="nsew", padx=(5, 0))
        for box in (title_box, artist_box):
            for c in range(2): box.columnconfigure(c, weight=1)

        ttk.Entry(title_box, textvariable=self.title_var).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self._num_control(title_box, "Größe (%)", self.title_size_var, 1, 15, 1, 0, increment=1)
        self._num_control(title_box, "X (%)", self.title_x_var, 0, 100, 3, 0, increment=1)
        self._num_control(title_box, "Y (%)", self.title_y_var, 0, 110, 5, 0, increment=1)
        self._num_control(title_box, "Zeichenabstand", self.title_spacing_var, -2, 20, 7, 0, increment=1)
        t_opts=ttk.Frame(title_box, style="Card.TFrame"); t_opts.grid(row=9,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(t_opts,text="Fett",variable=self.title_bold_var).pack(side="left",padx=(0,8)); ttk.Checkbutton(t_opts,text="GROSSBUCHSTABEN",variable=self.title_uppercase_var).pack(side="left")
        self._color_row(title_box, [("Farbe", "title", self.title_color_var)], row_start=10)

        ttk.Entry(artist_box, textvariable=self.artist_var).grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        self._num_control(artist_box, "Größe (%)", self.artist_size_var, 1, 15, 1, 0, increment=1)
        self._num_control(artist_box, "X (%)", self.artist_x_var, 0, 100, 3, 0, increment=1)
        self._num_control(artist_box, "Y (%)", self.artist_y_var, 0, 110, 5, 0, increment=1)
        self._num_control(artist_box, "Zeichenabstand", self.artist_spacing_var, -2, 20, 7, 0, increment=1)
        a_opts=ttk.Frame(artist_box, style="Card.TFrame"); a_opts.grid(row=9,column=0,columnspan=2,sticky="w")
        ttk.Checkbutton(a_opts,text="Fett",variable=self.artist_bold_var).pack(side="left",padx=(0,8)); ttk.Checkbutton(a_opts,text="GROSSBUCHSTABEN",variable=self.artist_uppercase_var).pack(side="left")
        self._color_row(artist_box, [("Farbe", "artist", self.artist_color_var)], row_start=10)

        text_fx = ttk.LabelFrame(text_tab, text="Text-Effekte", padding=8, style="Section.TLabelframe")
        text_fx.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        for c in range(4): text_fx.columnconfigure(c, weight=1)
        self._num_control(text_fx, "Schatten (%)", self.text_shadow_var, 0, 100, 0, 0, increment=1)
        self._num_control(text_fx, "Glow (%)", self.text_glow_var, 0, 100, 0, 2, increment=1)
        ttk.Label(text_fx, text="Titel und Künstler können einzeln oder gemeinsam als Textblock in der Vorschau verschoben werden.", wraplength=570).grid(row=2,column=0,columnspan=4,sticky="w",pady=(4,0))


        render = ttk.LabelFrame(left, text="3. Rendern", padding=12, style="Section.TLabelframe")
        render.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        render.columnconfigure(0, weight=1)
        ttk.Progressbar(render, variable=self.progress_var, maximum=100).grid(row=0, column=0, columnspan=3, sticky="ew")
        ttk.Label(render, textvariable=self.status_var).grid(row=1, column=0, sticky="w", pady=(7, 0))
        self.render_button = ttk.Button(render, text="MP4 mit Visualizer erstellen", command=self.start_render, style="Accent.TButton")
        self.render_button.grid(row=1, column=1, padx=(8, 0), pady=(7, 0))
        self.cancel_button = ttk.Button(render, text="Abbrechen", command=self.cancel_render, state="disabled", style="Secondary.TButton")
        self.cancel_button.grid(row=1, column=2, padx=(8, 0), pady=(7, 0))

        sysbox = ttk.LabelFrame(left, text="Video-Engine", padding=10, style="Section.TLabelframe")
        sysbox.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        sysbox.columnconfigure(0, weight=1)
        self.engine_status_label = ttk.Label(sysbox, textvariable=self.ffmpeg_status_var, style="Ready.TLabel")
        self.engine_status_label.grid(row=0, column=0, sticky="w")
        ttk.Button(sysbox, text="Erweitert …", command=self.show_engine_details, style="Secondary.TButton").grid(row=0, column=1, padx=(8, 0))

        preview = ttk.LabelFrame(right, text="Statische Vorschau", padding=12, style="Section.TLabelframe")
        preview.grid(row=0, column=0, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)

        self.preview_label = tk.Label(preview, text="Video auswählen …", anchor="center", bg=PANEL, fg=TEXT_MUTED, bd=1, relief="solid", highlightthickness=1, highlightbackground=BORDER)
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=2, pady=2)
        self._register_theme_widget(self.preview_label, "preview")
        self.preview_label.bind("<ButtonPress-1>", self._preview_mouse_down)
        self.preview_label.bind("<B1-Motion>", self._preview_mouse_move)
        self.preview_label.bind("<ButtonRelease-1>", self._preview_mouse_up)
        self.preview_label.bind("<Configure>", self._preview_widget_resized)

        timebar = ttk.Frame(preview)
        timebar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        timebar.columnconfigure(1, weight=1)
        ttk.Label(timebar, text="Videobild bei").grid(row=0, column=0, sticky="w")
        self.preview_time_scale = tk.Scale(
            timebar,
            variable=self.preview_time_var,
            from_=0,
            to=1,
            resolution=0.5,
            orient="horizontal",
            showvalue=False,
            highlightthickness=0,
            bg=SURFACE,
            fg=TEXT_MUTED,
            troughcolor=INPUT_BG,
            activebackground=ACCENT,
            command=self._preview_time_changed,
        )
        self.preview_time_scale.grid(row=0, column=1, sticky="ew", padx=(8, 8))
        self._register_theme_widget(self.preview_time_scale, "preview_scale")
        ttk.Label(timebar, textvariable=self.preview_time_text_var, width=8).grid(row=0, column=2, sticky="e")
        self.preview_button = ttk.Button(timebar, text="Bild neu laden", command=lambda: self._queue_frame_extract(force=True, delay_ms=0), style="Secondary.TButton")
        self.preview_button.grid(row=0, column=3, padx=(8, 0))

        dragbar = ttk.Frame(preview)
        dragbar.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(dragbar, text="Mit der Maus verschieben:").pack(side="left")
        ttk.Combobox(
            dragbar,
            textvariable=self.drag_target_var,
            values=["Automatisch", "Logo", "Cover", "Titel", "Künstler", "Titel & Künstler", "Visualizer"],
            state="readonly",
            width=18,
        ).pack(side="left", padx=(8, 0))
        ttk.Label(preview, text="Objekt im Vorschaubild anklicken und ziehen. Für beide Texte: „Titel & Künstler“ wählen.", wraplength=500).grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(
            preview,
            text="Die Balkenhöhen sind exemplarisch; Position, Breite, Farben, Logo, Cover, Effekte und Text entsprechen dem Export.",
            wraplength=500,
        ).grid(row=4, column=0, sticky="w", pady=(5, 0))
        ttk.Label(preview, textvariable=self.preview_status_var, wraplength=500).grid(row=5, column=0, sticky="w", pady=(5, 0))

    def _num_control(self, parent, label, var, minv, maxv, row, col, increment=1):
        box = ttk.Frame(parent, style="Card.TFrame")
        box.grid(
            row=row,
            column=col,
            columnspan=2,
            sticky="ew",
            padx=(0 if col == 0 else 10, 10 if col == 0 else 0),
            pady=(0, 6),
        )
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text=label, width=19, style="Section.TLabel").grid(row=0, column=0, sticky="w")
        scale = tk.Scale(
            box,
            variable=var,
            from_=minv,
            to=maxv,
            resolution=increment,
            orient="horizontal",
            showvalue=False,
            highlightthickness=0,
            length=150,
            sliderlength=18,
            width=7,
            bg=SURFACE,
            fg=TEXT_MUTED,
            troughcolor=INPUT_BG,
            activebackground=ACCENT,
            bd=0,
        )
        scale.grid(row=0, column=1, sticky="ew")
        self._register_theme_widget(scale, "scale")
        spin_kwargs = {
            "textvariable": var,
            "from_": minv,
            "to": maxv,
            "increment": increment,
            "width": 8,
            "justify": "center",
            "bg": INPUT_BG,
            "fg": TEXT,
            "insertbackground": TEXT,
            "buttonbackground": SURFACE_2,
            "highlightthickness": 1,
            "highlightbackground": BORDER,
            "relief": "flat",
        }
        if isinstance(increment, float) and not float(increment).is_integer():
            spin_kwargs["format"] = "%1.1f"
        spin = tk.Spinbox(box, **spin_kwargs)
        spin.grid(row=0, column=2, padx=(8, 0))
        self._register_theme_widget(spin, "spin")

    def _color_row(self, parent, specs, row_start=0):
        for idx, (label, key, var) in enumerate(specs):
            col = idx * 2
            ttk.Label(parent, text=label, style="Section.TLabel").grid(row=row_start, column=col, sticky="w", padx=(0 if idx == 0 else 10, 4))
            swatch = tk.Button(parent, text="   ", width=3, relief="solid", bd=1, command=lambda v=var, n=label: self.choose_color(v, n), bg="#ffffff", activebackground="#ffffff")
            swatch.grid(row=row_start, column=col + 1, sticky="w")
            self.color_buttons[key] = swatch
            entry = tk.Entry(parent, textvariable=var, width=10, bg=INPUT_BG, fg=TEXT, insertbackground=TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER)
            entry.grid(row=row_start + 1, column=col, columnspan=2, sticky="w", padx=(0 if idx == 0 else 10, 0), pady=(3, 0))
            self._register_theme_widget(entry, "entry")
            ttk.Button(parent, text="Pipette", command=lambda v=var, n=label: self.start_eyedropper(v, n), style="Secondary.TButton").grid(
                row=row_start + 2, column=col, columnspan=2, sticky="w", padx=(0 if idx == 0 else 10, 0), pady=(4, 0)
            )

    def _app_data_dir(self) -> Path:
        if os.name == "nt":
            root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
            path = root / "AudioVisualizerStudio"
        else:
            path = Path.home() / ".local" / "share" / "AudioVisualizerStudio"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _asset_store_dir(self) -> Path:
        path = self._app_data_dir() / "media_assets"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _asset_index_file(self) -> Path:
        return self._app_data_dir() / "asset_index.json"

    def _read_asset_index(self) -> dict:
        try:
            data = json.loads(self._asset_index_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_asset_index(self, data: dict) -> None:
        try:
            self._asset_index_file().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _asset_key(path: str) -> str:
        try:
            return os.path.normcase(os.path.abspath(os.path.expanduser(path.strip())))
        except Exception:
            return path.strip()

    def _persist_asset(self, source_path: str, kind: str) -> str:
        """Copy a chosen logo/cover into the permanent app asset store.

        The application then no longer depends on Downloads, OneDrive placeholders
        or renamed source folders after the image has been imported once.
        """
        source = Path(source_path).expanduser()
        if not source.is_file():
            return source_path
        try:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
        except Exception:
            digest = hashlib.sha256(str(source.resolve()).encode("utf-8", errors="ignore")).hexdigest()[:16]
        suffix = source.suffix.lower() if source.suffix else ".png"
        safe_kind = "logo" if kind == "logo" else "cover"
        destination = self._asset_store_dir() / f"{safe_kind}_{digest}{suffix}"
        if not destination.is_file():
            shutil.copy2(source, destination)
        index = self._read_asset_index()
        index[self._asset_key(str(source))] = str(destination)
        index[f"last_{safe_kind}"] = str(destination)
        self._write_asset_index(index)
        return str(destination)

    def _preset_asset_candidate(self, kind: str, preset_name: str | None = None) -> str | None:
        name = (preset_name or self.active_preset_name or self.preset_var.get()).strip()
        if not name:
            return None
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "preset"
        assets_dir = self._app_data_dir() / "preset_assets"
        for candidate in sorted(assets_dir.glob(f"{safe_name}_{kind}.*")):
            if candidate.is_file():
                return str(candidate)
        return None

    def _resolve_asset_path(self, raw_path: str, kind: str, *, preset_name: str | None = None, allow_last: bool = False) -> str | None:
        raw = str(raw_path or "").strip()
        if raw and Path(raw).is_file():
            return str(Path(raw).resolve())
        index = self._read_asset_index()
        if raw:
            mapped = index.get(self._asset_key(raw))
            if mapped and Path(str(mapped)).is_file():
                return str(Path(str(mapped)).resolve())
        preset_candidate = self._preset_asset_candidate(kind, preset_name=preset_name)
        if preset_candidate and Path(preset_candidate).is_file():
            return str(Path(preset_candidate).resolve())
        if allow_last:
            last = index.get(f"last_{kind}")
            if last and Path(str(last)).is_file():
                return str(Path(str(last)).resolve())
        return None

    def _repair_asset_paths(self, *, allow_last: bool = False) -> tuple[str | None, str | None]:
        def migrate_if_external(raw: str, kind: str) -> str:
            raw = str(raw or "").strip()
            if not raw or not Path(raw).is_file():
                return raw
            try:
                app_root = self._app_data_dir().resolve()
                resolved = Path(raw).resolve()
                if app_root not in resolved.parents:
                    return self._persist_asset(str(resolved), kind)
            except Exception:
                pass
            return raw

        raw_logo = migrate_if_external(self.logo_var.get(), "logo")
        raw_cover = migrate_if_external(self.cover_var.get(), "cover")
        logo = self._resolve_asset_path(raw_logo, "logo", allow_last=allow_last)
        cover = self._resolve_asset_path(raw_cover, "cover", allow_last=allow_last)
        if logo and logo != self.logo_var.get().strip():
            self.logo_var.set(logo)
        if cover and cover != self.cover_var.get().strip():
            self.cover_var.set(cover)
            self._update_cover_thumbnail(cover)
        return logo, cover

    def _settings_file(self) -> Path:
        return self._app_data_dir() / "app_settings.json"

    def _read_app_settings(self) -> dict:
        try:
            path = self._settings_file()
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _write_app_settings(self) -> None:
        try:
            self._settings_file().write_text(
                json.dumps(self.app_settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _session_lock_file(self) -> Path:
        return self._app_data_dir() / "session.lock"

    def _autosave_file(self) -> Path:
        return self._app_data_dir() / "autosave.json"

    def _tracked_vars(self) -> dict[str, tk.Variable]:
        return {
            "bars": self.bars_var, "width": self.width_var, "bar_width": self.bar_width_var,
            "height": self.height_var, "position": self.position_var, "opacity": self.opacity_var,
            "sensitivity": self.sensitivity_var, "visualizer_style": self.visualizer_style_var,
            "bass": self.bass_var, "mid": self.mid_var, "treble": self.treble_var, "smoothness": self.smoothness_var,
            "mirrored": self.mirrored_var, "glow": self.glow_var,
            "color_left": self.color_left_var, "color_mid": self.color_mid_var, "color_right": self.color_right_var,
            "show_logo": self.show_logo_var, "logo_path": self.logo_var, "logo_size": self.logo_size_var,
            "logo_content_scale": self.logo_content_scale_var, "logo_x": self.logo_x_var, "logo_y": self.logo_y_var,
            "remove_black": self.remove_black_var,
            "show_cover": self.show_cover_var, "cover_path": self.cover_var, "cover_size": self.cover_size_var,
            "cover_x": self.cover_x_var, "cover_y": self.cover_y_var, "cover_effect": self.cover_effect_var,
            "cover_corner": self.cover_corner_var, "cover_shadow_strength": self.cover_shadow_strength_var,
            "cover_shadow_angle": self.cover_shadow_angle_var, "cover_shadow_distance": self.cover_shadow_distance_var,
            "cover_frame_width": self.cover_frame_width_var, "cover_frame_color": self.cover_frame_color_var,
            "cover_glow_strength": self.cover_glow_strength_var, "cover_glow_color": self.cover_glow_color_var,
            "glass_enabled": self.glass_enabled_var, "glass_opacity": self.glass_opacity_var,
            "show_text": self.show_text_var, "artist": self.artist_var, "title": self.title_var, "font_name": self.font_name_var,
            "title_size": self.title_size_var, "artist_size": self.artist_size_var,
            "title_x": self.title_x_var, "title_y": self.title_y_var, "artist_x": self.artist_x_var, "artist_y": self.artist_y_var,
            "title_bold": self.title_bold_var, "artist_bold": self.artist_bold_var,
            "title_uppercase": self.title_uppercase_var, "artist_uppercase": self.artist_uppercase_var,
            "title_spacing": self.title_spacing_var, "artist_spacing": self.artist_spacing_var,
            "text_shadow": self.text_shadow_var, "text_glow": self.text_glow_var,
            "title_color": self.title_color_var, "artist_color": self.artist_color_var,
            "quality": self.quality_var,
        }

    def _snapshot_state(self) -> dict:
        result = {}
        for key, var in self._tracked_vars().items():
            try:
                result[key] = var.get()
            except Exception:
                pass
        return result

    def _apply_state(self, state: dict, *, update_history: bool = True) -> None:
        self._history_suspend = True
        try:
            for key, var in self._tracked_vars().items():
                if key in state:
                    try:
                        var.set(state[key])
                    except Exception:
                        pass
        finally:
            self._history_suspend = False
        if update_history:
            self._history_last_snapshot = self._snapshot_state()
        self._repair_asset_paths(allow_last=False)
        self._update_cover_thumbnail()
        self._refresh_static_preview()

    def _queue_history_commit(self) -> None:
        if self._history_suspend:
            return
        if self._history_after_id is not None:
            try:
                self.after_cancel(self._history_after_id)
            except tk.TclError:
                pass
        self._history_after_id = self.after(250, self._commit_history)

    def _commit_history(self) -> None:
        self._history_after_id = None
        if self._history_suspend:
            return
        current = self._snapshot_state()
        if self._history_last_snapshot is None:
            self._history_last_snapshot = current
            return
        if current != self._history_last_snapshot:
            self.undo_stack.append(dict(self._history_last_snapshot))
            if len(self.undo_stack) > 100:
                self.undo_stack = self.undo_stack[-100:]
            self.redo_stack.clear()
            self._history_last_snapshot = current

    def undo(self, _event=None):
        if not self.undo_stack:
            return "break" if _event is not None else None
        current = self._snapshot_state()
        target = self.undo_stack.pop()
        self.redo_stack.append(current)
        self._apply_state(target)
        self.status_var.set("Rückgängig")
        return "break" if _event is not None else None

    def redo(self, _event=None):
        if not self.redo_stack:
            return "break" if _event is not None else None
        current = self._snapshot_state()
        target = self.redo_stack.pop()
        self.undo_stack.append(current)
        self._apply_state(target)
        self.status_var.set("Wiederholt")
        return "break" if _event is not None else None

    def _autosave_payload(self) -> dict:
        return {
            "version": 1,
            "input": self.input_var.get(),
            "output": self.output_var.get(),
            "state": self._snapshot_state(),
        }

    def _write_autosave(self) -> None:
        try:
            self._autosave_file().write_text(json.dumps(self._autosave_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _schedule_autosave(self) -> None:
        self._write_autosave()
        self._autosave_after_id = self.after(10000, self._schedule_autosave)

    def _offer_autosave_recovery(self) -> None:
        if not self._session_was_unclean or not self._autosave_file().is_file():
            return
        try:
            data = json.loads(self._autosave_file().read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict) or not isinstance(data.get("state"), dict):
            return
        if not messagebox.askyesno(APP_TITLE, "Die letzte Sitzung wurde nicht sauber beendet. Letzten Arbeitsstand wiederherstellen?"):
            return
        self._apply_state(data.get("state", {}))
        input_path = str(data.get("input", "")).strip()
        output_path = str(data.get("output", "")).strip()
        if input_path and os.path.isfile(input_path):
            self.input_var.set(input_path)
            self.output_var.set(output_path)
            self.inspect_video(input_path)
        self.status_var.set("Letzte Sitzung wiederhergestellt")

    def _preset_file(self) -> Path:
        return self._app_data_dir() / "presets.json"

    def _read_presets(self) -> dict:
        try:
            data = json.loads(self._preset_file().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except Exception:
            return {}

    def _write_presets(self, presets: dict) -> None:
        self._preset_file().write_text(json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_presets_into_ui(self):
        presets = self._read_presets()
        names = sorted(presets.keys(), key=str.lower)
        if hasattr(self, "preset_combo"):
            self.preset_combo.configure(values=names)
        if self.preset_var.get() not in names:
            self.preset_var.set(names[0] if names else "")

    def _preset_design_state(self) -> dict:
        """Return reusable design/layout values for a preset.

        Track-specific content such as the selected video, cover file, artist name
        and title text is intentionally not stored. Their styling and exact
        positions are stored, however, so a preset can be reused on another song.
        """
        state = self._snapshot_state()
        excluded = {
            "artist",
            "title",
        }
        return {key: value for key, value in state.items() if key not in excluded}

    def save_preset(self):
        presets = self._read_presets()
        name: str | None = None

        if self.active_preset_name and self.active_preset_name in presets:
            answer = messagebox.askyesnocancel(
                APP_TITLE,
                f"Das Preset '{self.active_preset_name}' ist aktuell aktiv.\n\n"
                "Möchtest du das bestehende Preset überschreiben?\n\n"
                "Ja = überschreiben\nNein = als neues Preset speichern\nAbbrechen = nichts speichern",
                parent=self,
            )
            if answer is None:
                return
            if answer:
                name = self.active_preset_name
            else:
                name = simpledialog.askstring(APP_TITLE, "Name für das neue Preset:", parent=self)
        else:
            name = simpledialog.askstring(APP_TITLE, "Name für das Preset:", parent=self)

        if not name:
            return
        name = name.strip()
        if not name:
            return

        if name in presets and name != self.active_preset_name:
            if not messagebox.askyesno(APP_TITLE, f"Preset '{name}' existiert bereits. Überschreiben?", parent=self):
                return

        # Presets get their own durable copies of both artwork files. This is
        # intentionally separate from the managed import store so a preset stays
        # self-contained even if the originally selected file later moves.
        assets_dir = self._app_data_dir() / "preset_assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "preset"

        def copy_preset_asset(raw_path: str, kind: str) -> str:
            resolved = self._resolve_asset_path(raw_path, kind, allow_last=False)
            if not resolved:
                return raw_path.strip()
            suffix = Path(resolved).suffix.lower() or ".png"
            copied = assets_dir / f"{safe_name}_{kind}{suffix}"
            shutil.copy2(resolved, copied)
            return str(copied)

        logo_path_for_preset = self.logo_var.get().strip()
        cover_path_for_preset = self.cover_var.get().strip()
        try:
            if logo_path_for_preset:
                logo_path_for_preset = copy_preset_asset(logo_path_for_preset, "logo")
            if cover_path_for_preset:
                cover_path_for_preset = copy_preset_asset(cover_path_for_preset, "cover")
        except Exception as exc:
            if not messagebox.askyesno(
                APP_TITLE,
                f"Logo/Cover konnten nicht vollständig in das Preset kopiert werden:\n{exc}\n\nPreset trotzdem mit den vorhandenen Dateipfaden speichern?",
                parent=self,
            ):
                return

        design_state = self._preset_design_state()
        design_state["logo_path"] = logo_path_for_preset
        design_state["cover_path"] = cover_path_for_preset

        presets[name] = {
            "schema_version": 3,
            "design_state": design_state,
            # Keep the older sections for backward compatibility and for clean-up
            # of copied preset logo assets.
            "colors": {
                "left": self.color_left_var.get(),
                "mid": self.color_mid_var.get(),
                "right": self.color_right_var.get(),
                "title": self.title_color_var.get(),
                "artist": self.artist_color_var.get(),
            },
            "logo": {
                "show": bool(self.show_logo_var.get()),
                "path": logo_path_for_preset,
                "badge_size": int(self.logo_size_var.get()),
                "content_scale": int(self.logo_content_scale_var.get()),
                "x": int(self.logo_x_var.get()),
                "y": int(self.logo_y_var.get()),
                "remove_black": bool(self.remove_black_var.get()),
            },
            "cover": {
                "show": bool(self.show_cover_var.get()),
                "path": cover_path_for_preset,
                "size": int(self.cover_size_var.get()),
                "x": int(self.cover_x_var.get()),
                "y": int(self.cover_y_var.get()),
            },
        }
        try:
            self._write_presets(presets)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Preset konnte nicht gespeichert werden: {exc}")
            return
        self._load_presets_into_ui()
        self.preset_var.set(name)
        self.active_preset_name = name
        self._history_last_snapshot = self._snapshot_state()
        self.status_var.set(f"Preset gespeichert: {name}")

    def load_preset(self):
        name = self.preset_var.get().strip()
        if not name:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst ein Preset auswählen.")
            return
        preset = self._read_presets().get(name)
        if not isinstance(preset, dict):
            messagebox.showwarning(APP_TITLE, "Preset wurde nicht gefunden.")
            self._load_presets_into_ui()
            return

        missing_logo = False
        missing_cover = False
        legacy_preset = False
        design_state = preset.get("design_state")
        if isinstance(design_state, dict):
            state = dict(design_state)

            raw_logo = str(state.get("logo_path", "")).strip()
            if raw_logo:
                resolved_logo = self._resolve_asset_path(raw_logo, "logo", preset_name=name, allow_last=False)
                if resolved_logo:
                    state["logo_path"] = resolved_logo
                else:
                    missing_logo = True
                    state.pop("logo_path", None)

            raw_cover = str(state.get("cover_path", "")).strip()
            if raw_cover:
                resolved_cover = self._resolve_asset_path(raw_cover, "cover", preset_name=name, allow_last=False)
                if resolved_cover:
                    state["cover_path"] = resolved_cover
                else:
                    missing_cover = True
                    state.pop("cover_path", None)
            else:
                # v1.9.4 and older did not store the cover path in the design state.
                # If a durable preset cover exists, recover it automatically.
                recovered_cover = self._preset_asset_candidate("cover", preset_name=name)
                if recovered_cover:
                    state["cover_path"] = recovered_cover

            self._apply_state(state)
        else:
            legacy_preset = True
            # Migration path for presets created by v1.9.1 and earlier. Those
            # versions only stored colors and logo values; positions for the
            # visualizer, cover and text simply did not exist in the preset file.
            colors = preset.get("colors", {}) if isinstance(preset.get("colors", {}), dict) else {}
            logo = preset.get("logo", {}) if isinstance(preset.get("logo", {}), dict) else {}
            legacy_state = {
                "color_left": colors.get("left", self.color_left_var.get()),
                "color_mid": colors.get("mid", self.color_mid_var.get()),
                "color_right": colors.get("right", self.color_right_var.get()),
                "title_color": colors.get("title", self.title_color_var.get()),
                "artist_color": colors.get("artist", self.artist_color_var.get()),
                "show_logo": bool(logo.get("show", self.show_logo_var.get())),
                "logo_size": logo.get("badge_size", self.logo_size_var.get()),
                "logo_content_scale": logo.get("content_scale", self.logo_content_scale_var.get()),
                "logo_x": logo.get("x", self.logo_x_var.get()),
                "logo_y": logo.get("y", self.logo_y_var.get()),
                "remove_black": bool(logo.get("remove_black", self.remove_black_var.get())),
            }
            logo_path = str(logo.get("path", "")).strip()
            if logo_path:
                resolved_logo = self._resolve_asset_path(logo_path, "logo", preset_name=name, allow_last=False)
                if resolved_logo:
                    legacy_state["logo_path"] = resolved_logo
                else:
                    missing_logo = True
            recovered_cover = self._preset_asset_candidate("cover", preset_name=name)
            if recovered_cover:
                legacy_state["cover_path"] = recovered_cover
                legacy_state["show_cover"] = True
            self._apply_state(legacy_state)

        self.active_preset_name = name
        self._history_last_snapshot = self._snapshot_state()
        self.status_var.set(f"Preset geladen: {name}")
        if missing_logo or missing_cover:
            missing = []
            if missing_logo:
                missing.append("Logo")
            if missing_cover:
                missing.append("Cover")
            messagebox.showwarning(
                APP_TITLE,
                f"Folgende im Preset gespeicherte Datei(en) wurden nicht mehr gefunden: {', '.join(missing)}.\n\n"
                "Die übrigen Preset-Einstellungen wurden trotzdem geladen. Wähle die Datei einmal neu aus; v1.9.5 speichert sie danach dauerhaft in der Anwendung.",
            )
        if legacy_preset:
            messagebox.showinfo(
                APP_TITLE,
                "Dieses Preset wurde mit einer älteren Version gespeichert. Damals wurden die Positionen von Visualizer, Cover, Titel und Künstler noch nicht im Preset abgelegt.\n\n"
                "Bitte die Positionen einmal einstellen und das Preset anschließend überschreiben. Ab v1.9.2 werden diese Werte vollständig gespeichert.",
                parent=self,
            )

    def delete_preset(self):
        name = self.preset_var.get().strip()
        if not name:
            return
        if not messagebox.askyesno(APP_TITLE, f"Preset '{name}' wirklich löschen?"):
            return
        presets = self._read_presets()
        removed = presets.pop(name, None)
        try:
            if isinstance(removed, dict):
                assets_dir = (self._app_data_dir() / "preset_assets").resolve()
                for kind in ("logo", "cover"):
                    section = removed.get(kind, {}) if isinstance(removed.get(kind, {}), dict) else {}
                    saved_asset = str(section.get("path", "")).strip()
                    if not saved_asset:
                        continue
                    saved_path = Path(saved_asset)
                    try:
                        if saved_path.is_file() and assets_dir in saved_path.resolve().parents:
                            still_used = False
                            for other in presets.values():
                                if not isinstance(other, dict):
                                    continue
                                other_section = other.get(kind, {}) if isinstance(other.get(kind, {}), dict) else {}
                                if str(other_section.get("path", "")).strip() == saved_asset:
                                    still_used = True
                                    break
                            if not still_used:
                                saved_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            self._write_presets(presets)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Preset konnte nicht gelöscht werden: {exc}")
            return
        if self.active_preset_name == name:
            self.active_preset_name = None
        self.preset_var.set("")
        self._load_presets_into_ui()
        self.status_var.set("Preset gelöscht")

    def _bind_preview_updates(self):
        for var in self._tracked_vars().values():
            var.trace_add("write", self._preview_setting_changed)

    def _preview_setting_changed(self, *_args):
        self._update_color_swatches()
        if self.redraw_after_id is not None:
            try:
                self.after_cancel(self.redraw_after_id)
            except tk.TclError:
                pass
        self.redraw_after_id = self.after(35, self._refresh_static_preview)
        self._queue_history_commit()

    def choose_color(self, variable: tk.StringVar, label: str):
        initial = variable.get() if HEX_RE.match(variable.get().strip()) else "#ffffff"
        _rgb, hex_value = colorchooser.askcolor(color=initial, title=f"Farbe {label} wählen")
        if hex_value:
            variable.set(hex_value.lower())


    def start_eyedropper(self, variable: tk.StringVar, label: str):
        if self.preview_composed_image is None:
            messagebox.showinfo(APP_TITLE, "Bitte zuerst ein Video auswählen, damit rechts ein Vorschaubild vorhanden ist.")
            return
        self.eyedropper_target = variable
        try:
            self.preview_label.configure(cursor="crosshair")
        except Exception:
            pass
        self.preview_status_var.set(f"Pipette für {label}: gewünschte Farbe rechts im Vorschaubild anklicken.")

    def _pick_color_from_preview(self, x: int, y: int) -> bool:
        if self.eyedropper_target is None or self.preview_composed_image is None:
            return False
        img = self.preview_composed_image.convert("RGB")
        x = max(0, min(img.width - 1, int(x)))
        y = max(0, min(img.height - 1, int(y)))
        r, g, b = img.getpixel((x, y))
        self.eyedropper_target.set(f"#{r:02x}{g:02x}{b:02x}")
        self.eyedropper_target = None
        try:
            self.preview_label.configure(cursor="")
        except Exception:
            pass
        self.preview_status_var.set("Farbe aus dem Vorschaubild übernommen.")
        return True

    def _update_color_swatches(self):
        mapping = {
            "left": self.color_left_var.get().strip(),
            "mid": self.color_mid_var.get().strip(),
            "right": self.color_right_var.get().strip(),
            "title": self.title_color_var.get().strip(),
            "artist": self.artist_color_var.get().strip(),
            "cover_frame": self.cover_frame_color_var.get().strip(),
            "cover_glow": self.cover_glow_color_var.get().strip(),
        }
        for key, value in mapping.items():
            button = self.color_buttons.get(key)
            if button and HEX_RE.match(value):
                try:
                    button.configure(bg=value, activebackground=value)
                except tk.TclError:
                    pass

    def _preview_time_changed(self, value: str):
        try:
            seconds = float(value)
        except ValueError:
            seconds = 0.0
        self.preview_time_text_var.set(self._format_time(seconds))
        self._queue_frame_extract(force=False, delay_ms=250)

    @staticmethod
    def _format_time(seconds: float) -> str:
        seconds = max(0.0, seconds)
        mins = int(seconds // 60)
        secs = seconds - mins * 60
        return f"{mins:02d}:{secs:04.1f}"

    def _update_ffmpeg_status(self):
        self.renderer = Renderer()
        ready = bool(self.renderer.ffmpeg_path and self.renderer.ffprobe_path)
        if ready:
            self.ffmpeg_status_var.set("● Video-Engine bereit")
            if hasattr(self, "engine_status_label"):
                try:
                    self.engine_status_label.configure(style="Ready.TLabel")
                except Exception:
                    pass
        else:
            self.ffmpeg_status_var.set("● Video-Engine nicht verfügbar")
            if hasattr(self, "engine_status_label"):
                try:
                    self.engine_status_label.configure(style="Error.TLabel")
                except Exception:
                    pass

    def show_engine_details(self):
        """Advanced diagnostics only; normal users never see FFmpeg paths."""
        self.renderer = Renderer()
        win = tk.Toplevel(self)
        win.title("Einstellungen – Erweitert – Diagnose")
        win.transient(self)
        win.grab_set()
        win.geometry("760x420")
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)

        ready = bool(self.renderer.ffmpeg_path and self.renderer.ffprobe_path)
        ttk.Label(
            frame,
            text="Video-Engine bereit" if ready else "Video-Engine nicht verfügbar",
            style="Ready.TLabel" if ready else "Error.TLabel",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Technische Informationen für Diagnose und Support. Im normalen Arbeitsbereich werden diese Details bewusst ausgeblendet.",
            wraplength=700,
        ).pack(anchor="w", pady=(8, 14))

        version = "2.1.0"
        details = [
            f"Audio Visualizer Studio: {version}",
            f"Programm: {sys.executable if getattr(sys, 'frozen', False) else Path(__file__).resolve()}",
            f"Video-Engine: {self.renderer.ffmpeg_path or 'nicht gefunden'}",
            f"Analyse-Engine: {self.renderer.ffprobe_path or 'nicht gefunden'}",
            f"Benutzerdaten: {self._app_data_dir()}",
            f"Betriebssystem: {sys.platform}",
        ]
        box = tk.Text(frame, height=10, wrap="word")
        box.pack(fill="both", expand=True)
        box.insert("1.0", "\n".join(details))
        box.configure(state="disabled")
        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="Schließen", command=win.destroy, style="Secondary.TButton").pack(side="right")

    def choose_video(self):
        path = filedialog.askopenfilename(title="Video auswählen", filetypes=[("Video", "*.mp4 *.mov *.mkv *.m4v"), ("Alle Dateien", "*.*")])
        if not path:
            return
        self.input_var.set(path)
        source = Path(path)
        self.output_var.set(str(source.with_name(source.stem + "_visualizer.mp4")))
        self.inspect_video(path)

    def inspect_video(self, path: str):
        try:
            self.renderer.validate()
            meta = probe_media(path, self.renderer.ffprobe_path or "")
            self.media_meta = meta
            mins = int(meta["duration"] // 60)
            secs = int(meta["duration"] % 60)
            self.media_info_var.set(
                f"{meta['width']}×{meta['height']} · {meta['fps']:.2f} fps · {mins}:{secs:02d} · Audio: {meta['audio_codec']}"
            )
            max_preview_start = max(0.0, float(meta["duration"]) - 0.1)
            self.preview_time_scale.configure(to=max(0.5, max_preview_start))
            initial = min(max_preview_start, max(0.0, float(meta["duration"]) * 0.15))
            self.preview_time_var.set(initial)
            self.preview_time_text_var.set(self._format_time(initial))
            self._queue_frame_extract(force=True, delay_ms=50)
        except Exception as exc:
            self.media_meta = None
            self.preview_base_image = None
            self.media_info_var.set(str(exc))
            self.preview_status_var.set(f"Vorschau nicht verfügbar: {exc}")

    def choose_logo(self):
        path = filedialog.askopenfilename(title="Logo auswählen", filetypes=[("Bilder", "*.png *.jpg *.jpeg *.webp"), ("Alle Dateien", "*.*")])
        if path:
            managed = self._persist_asset(path, "logo")
            self.logo_var.set(managed)
            self.show_logo_var.set(True)
            self.status_var.set("Logo importiert und dauerhaft gespeichert")

    def choose_cover(self):
        path = filedialog.askopenfilename(title="Cover auswählen", filetypes=[("Bilder", "*.png *.jpg *.jpeg *.webp"), ("Alle Dateien", "*.*")])
        if path:
            managed = self._persist_asset(path, "cover")
            self.cover_var.set(managed)
            self.show_cover_var.set(True)
            self._update_cover_thumbnail(managed)
            self.status_var.set("Cover importiert und dauerhaft gespeichert")

    def _update_cover_thumbnail(self, path: str | None = None):
        path = path or self.cover_var.get().strip()
        if not path or not os.path.isfile(path):
            self.cover_thumb_photo = None
            self.cover_thumb_label.configure(image="", text="Keine Cover-Vorschau")
            return
        try:
            thumb = make_cover_image(path, 96)
            self.cover_thumb_photo = ImageTk.PhotoImage(thumb)
            self.cover_thumb_label.configure(image=self.cover_thumb_photo, text="")
        except Exception:
            self.cover_thumb_photo = None
            self.cover_thumb_label.configure(image="", text="Cover nicht lesbar")

    def choose_output(self):
        path = filedialog.asksaveasfilename(title="Ausgabe speichern", defaultextension=".mp4", filetypes=[("MP4", "*.mp4")])
        if path:
            self.output_var.set(path)

    def _settings(self) -> RenderSettings:
        quality = self.quality_var.get()
        if quality == "Sehr hohe Qualität":
            preset, crf = "slow", 16
        elif quality == "Schneller Export":
            preset, crf = "veryfast", 21
        else:
            preset, crf = "medium", 18
        return RenderSettings(
            bars=self.bars_var.get(), width_pct=self.width_var.get(), bar_width_pct=self.bar_width_var.get(),
            height_pct=self.height_var.get(), vertical_position_pct=self.position_var.get(), opacity_pct=self.opacity_var.get(),
            sensitivity=self.sensitivity_var.get(), visualizer_style=self.visualizer_style_var.get(),
            bass_weight_pct=self.bass_var.get(), mid_weight_pct=self.mid_var.get(), treble_weight_pct=self.treble_var.get(),
            smoothness_pct=self.smoothness_var.get(), mirrored=self.mirrored_var.get(), glow=self.glow_var.get(),
            show_logo=self.show_logo_var.get(), logo_size_pct=self.logo_size_var.get(),
            logo_content_scale_pct=self.logo_content_scale_var.get(), logo_x_pct=self.logo_x_var.get(), logo_y_pct=self.logo_y_var.get(),
            remove_black_logo_bg=self.remove_black_var.get(),
            show_cover=self.show_cover_var.get(), cover_size_pct=self.cover_size_var.get(), cover_x_pct=self.cover_x_var.get(),
            cover_y_pct=self.cover_y_var.get(), cover_effect=self.cover_effect_var.get(), cover_corner_pct=self.cover_corner_var.get(),
            cover_shadow_strength_pct=self.cover_shadow_strength_var.get(), cover_shadow_angle_deg=self.cover_shadow_angle_var.get(),
            cover_shadow_distance_pct=self.cover_shadow_distance_var.get(), cover_frame_width_pct=self.cover_frame_width_var.get(),
            cover_frame_color=self.cover_frame_color_var.get(), cover_glow_strength_pct=self.cover_glow_strength_var.get(),
            cover_glow_color=self.cover_glow_color_var.get(), glass_enabled=self.glass_enabled_var.get(), glass_opacity_pct=self.glass_opacity_var.get(),
            show_text=self.show_text_var.get(), artist=self.artist_var.get(), title=self.title_var.get(), font_name=self.font_name_var.get(),
            title_size_pct=self.title_size_var.get(), artist_size_pct=self.artist_size_var.get(),
            title_x_pct=self.title_x_var.get(), title_y_pct=self.title_y_var.get(), artist_x_pct=self.artist_x_var.get(), artist_y_pct=self.artist_y_var.get(),
            title_bold=self.title_bold_var.get(), artist_bold=self.artist_bold_var.get(),
            title_uppercase=self.title_uppercase_var.get(), artist_uppercase=self.artist_uppercase_var.get(),
            title_letter_spacing=self.title_spacing_var.get(), artist_letter_spacing=self.artist_spacing_var.get(),
            text_shadow_pct=self.text_shadow_var.get(), text_glow_pct=self.text_glow_var.get(),
            title_color=self.title_color_var.get(), artist_color=self.artist_color_var.get(),
            color_left=self.color_left_var.get(), color_mid=self.color_mid_var.get(), color_right=self.color_right_var.get(),
            video_preset=preset, crf=crf,
        )

    def _queue_frame_extract(self, force: bool, delay_ms: int = 250):
        if not self.input_var.get().strip():
            return
        self.frame_request_id += 1
        request_id = self.frame_request_id
        if self.frame_thread and self.frame_thread.is_alive():
            try:
                self.renderer.cancel_preview()
            except Exception:
                pass
        if self.frame_after_id is not None:
            try:
                self.after_cancel(self.frame_after_id)
            except tk.TclError:
                pass
        self.frame_after_id = self.after(delay_ms, lambda rid=request_id: self._start_frame_extract(rid))

    def _start_frame_extract(self, request_id: int):
        self.frame_after_id = None
        if request_id != self.frame_request_id:
            return
        if self.frame_thread and self.frame_thread.is_alive():
            self.after(80, self._start_frame_extract, request_id)
            return

        input_path = self.input_var.get().strip()
        if not input_path:
            return
        start_seconds = self.preview_time_var.get()
        image_path = os.path.join(self.preview_temp.name, f"frame_{request_id}.jpg")
        self.preview_status_var.set("Videobild wird einmalig geladen …")
        self.preview_button.configure(state="disabled")

        def worker():
            try:
                self.renderer.extract_preview_frame(
                    input_video=input_path,
                    output_image=image_path,
                    start_seconds=start_seconds,
                    max_width=860,
                    media_meta=self.media_meta,
                )
            except Exception as exc:
                self.after(0, self._frame_failed, request_id, str(exc))
            else:
                self.after(0, self._frame_done, request_id, image_path)

        self.frame_thread = threading.Thread(target=worker, daemon=True)
        self.frame_thread.start()

    def _frame_failed(self, request_id: int, message: str):
        self.frame_thread = None
        self.preview_button.configure(state="normal")
        if request_id == self.frame_request_id:
            short = message.strip().splitlines()[-1] if message.strip() else "Unbekannter Fehler"
            self.preview_status_var.set(f"Vorschaufehler: {short}")

    def _frame_done(self, request_id: int, image_path: str):
        self.frame_thread = None
        self.preview_button.configure(state="normal")
        if request_id != self.frame_request_id:
            return
        try:
            with Image.open(image_path) as img:
                self.preview_base_image = img.convert("RGB").copy()
            self.preview_status_var.set("Videobild geladen · Änderungen werden jetzt sofort angezeigt.")
            self._refresh_static_preview()
        except Exception as exc:
            self.preview_status_var.set(f"Vorschaubild konnte nicht angezeigt werden: {exc}")

    def _badge_for_preview(self, settings: RenderSettings, logo_path: str | None) -> Image.Image | None:
        if not settings.show_logo or not logo_path or not os.path.isfile(logo_path) or self.preview_design_base_image is None:
            return None
        width, height = self.preview_design_base_image.size
        badge_size = int(min(width, height) * settings.logo_size_pct / 100)
        badge_size = max(64, min(int(min(width, height) * 0.55), badge_size))
        key = (
            os.path.abspath(logo_path),
            os.path.getmtime(logo_path),
            badge_size,
            settings.logo_content_scale_pct,
            settings.color_left.lower(),
            settings.color_mid.lower(),
            settings.color_right.lower(),
            settings.remove_black_logo_bg,
        )
        if key != self.badge_cache_key or self.badge_cache_image is None:
            self.badge_cache_image = make_logo_badge_image(
                logo_path=logo_path,
                size=badge_size,
                color_left=settings.color_left,
                color_mid=settings.color_mid,
                color_right=settings.color_right,
                remove_black=settings.remove_black_logo_bg,
                logo_content_scale_pct=settings.logo_content_scale_pct,
            )
            self.badge_cache_key = key
        return self.badge_cache_image

    def _cover_for_preview(self, settings: RenderSettings, cover_path: str | None) -> Image.Image | None:
        if not settings.show_cover or not cover_path or not os.path.isfile(cover_path) or self.preview_design_base_image is None:
            return None
        width, height = self.preview_design_base_image.size
        cover_size = int(min(width, height) * settings.cover_size_pct / 100)
        cover_size = max(48, min(int(min(width, height) * 0.65), cover_size))
        key = (os.path.abspath(cover_path), os.path.getmtime(cover_path), cover_size)
        if key != self.cover_cache_key or self.cover_cache_image is None:
            self.cover_cache_image = make_cover_image(cover_path, cover_size)
            self.cover_cache_key = key
        return self.cover_cache_image

    def _refresh_static_preview(self):
        self.redraw_after_id = None
        if self.preview_base_image is None:
            return
        settings = self._settings()
        for value in (settings.color_left, settings.color_mid, settings.color_right, settings.title_color, settings.artist_color, settings.cover_frame_color, settings.cover_glow_color):
            if not HEX_RE.match(value.strip()):
                self.preview_status_var.set("Bitte Farben als #RRGGBB eingeben oder über das Farbfeld auswählen.")
                return
        resolved_logo, resolved_cover = self._repair_asset_paths(allow_last=False)
        logo_path = resolved_logo or (self.logo_var.get().strip() or None)
        cover_path = resolved_cover or (self.cover_var.get().strip() or None)
        try:
            if self.media_meta:
                design_w, design_h = design_reference_size(int(self.media_meta["width"]), int(self.media_meta["height"]))
            else:
                design_w, design_h = self.preview_base_image.size
            if self.preview_base_image.size != (design_w, design_h):
                self.preview_design_base_image = self.preview_base_image.resize((design_w, design_h), Image.Resampling.BILINEAR)
            else:
                self.preview_design_base_image = self.preview_base_image
            badge = self._badge_for_preview(settings, logo_path)
            cover = self._cover_for_preview(settings, cover_path)
            composed = make_static_preview(
                self.preview_design_base_image,
                settings,
                logo_path=logo_path,
                cover_path=cover_path,
                badge_override=badge,
                cover_override=cover,
            )
            self.preview_composed_image = composed.copy()
            self._update_preview_display()
            self.preview_status_var.set(
                "Statische Vorschau aktuell · Das komplette Videobild wird eingepasst. Objekte können über die gesamte Bildfläche positioniert werden."
            )
        except Exception as exc:
            self.preview_status_var.set(f"Vorschau konnte nicht aktualisiert werden: {exc}")

    def _preview_widget_resized(self, _event=None):
        if self._preview_resize_after_id is not None:
            try:
                self.after_cancel(self._preview_resize_after_id)
            except tk.TclError:
                pass
        self._preview_resize_after_id = self.after(80, self._update_preview_display)

    def _update_preview_display(self):
        self._preview_resize_after_id = None
        if self.preview_composed_image is None:
            return
        source = self.preview_composed_image
        widget_w = max(1, self.preview_label.winfo_width())
        widget_h = max(1, self.preview_label.winfo_height())

        # Keep a small inner margin and always fit the ENTIRE video frame.
        # This prevents wide 16:9 / ultrawide videos from being clipped by the
        # preview widget and keeps mouse coordinates valid across the whole frame.
        avail_w = max(120, widget_w - 18)
        avail_h = max(90, widget_h - 18)
        scale = min(avail_w / source.width, avail_h / source.height, 1.0)
        if widget_w < 30 or widget_h < 30:
            scale = 1.0
        display_w = max(1, int(round(source.width * scale)))
        display_h = max(1, int(round(source.height * scale)))
        if (display_w, display_h) != source.size:
            display = source.resize((display_w, display_h), Image.Resampling.LANCZOS)
        else:
            display = source
        self.preview_display_image = display
        self.preview_display_scale = display_w / max(1, source.width)
        self.preview_display_offset = (
            max(0, (widget_w - display_w) // 2),
            max(0, (widget_h - display_h) // 2),
        )
        self.preview_photo = ImageTk.PhotoImage(display)
        self.preview_label.configure(image=self.preview_photo, text="")

    def _image_coords_from_event(self, event) -> tuple[int, int] | None:
        if self.preview_base_image is None or self.preview_photo is None or self.preview_composed_image is None:
            return None
        display_w, display_h = self.preview_photo.width(), self.preview_photo.height()
        widget_w = self.preview_label.winfo_width()
        widget_h = self.preview_label.winfo_height()
        off_x = max(0, (widget_w - display_w) // 2)
        off_y = max(0, (widget_h - display_h) // 2)
        dx = event.x - off_x
        dy = event.y - off_y
        if dx < 0 or dy < 0 or dx >= display_w or dy >= display_h:
            return None
        sx = self.preview_composed_image.width / max(1, display_w)
        sy = self.preview_composed_image.height / max(1, display_h)
        x = int(max(0, min(self.preview_composed_image.width - 1, round(dx * sx))))
        y = int(max(0, min(self.preview_composed_image.height - 1, round(dy * sy))))
        return x, y

    def _current_layout(self):
        canvas = self.preview_composed_image or self.preview_design_base_image or self.preview_base_image
        if canvas is None:
            return {}
        settings = self._settings()
        logo_path = self.logo_var.get().strip() or None
        cover_path = self.cover_var.get().strip() or None
        layout = measure_artwork_layout(canvas.width, canvas.height, settings, logo_path=logo_path, cover_path=cover_path)
        geom = compute_visualizer_layout(canvas.width, canvas.height, settings)
        style = (settings.visualizer_style or "Klassische Balken").lower()
        centered_style = ("kreis" in style or "ring" in style or "waveform" in style or "doppelwelle" in style)
        vis_y = geom["y_centered"] if settings.mirrored or centered_style else geom["y_one_sided"]
        layout["visualizer"] = {"x": geom["x"], "y": vis_y, "w": geom["width"], "h": geom["height"]}
        return layout

    def _resolve_drag_target(self, x: int, y: int, layout: dict) -> str | None:
        selected = self.drag_target_var.get().strip().lower()
        aliases={"künstler":"artist","titel":"title","titel & künstler":"text","text":"text","visualizer":"visualizer","logo":"logo","cover":"cover"}
        selected=aliases.get(selected,selected)
        if selected in ("logo","cover","title","artist","text","visualizer") and selected in layout:
            return selected
        if selected == "automatisch":
            for key in ("logo","cover","title","artist","visualizer"):
                box=layout.get(key)
                if box and box["x"] <= x <= box["x"]+box["w"] and box["y"] <= y <= box["y"]+box["h"]:
                    return key
        return None

    def _set_center_pct(self, key: str, center_x: float, center_y: float):
        canvas = self.preview_composed_image or self.preview_design_base_image or self.preview_base_image
        if canvas is None:
            return
        width,height=canvas.size
        px=int(round(max(0,min(width,center_x))*100/max(1,width)))
        py=int(round(max(0,min(height*1.10,center_y))*100/max(1,height)))
        if key=="logo":
            self.logo_x_var.set(px); self.logo_y_var.set(py)
        elif key=="cover":
            self.cover_x_var.set(px); self.cover_y_var.set(py)
        elif key=="title":
            self.title_x_var.set(px); self.title_y_var.set(py)
        elif key=="artist":
            self.artist_x_var.set(px); self.artist_y_var.set(py)
        elif key=="visualizer":
            self.position_var.set(py)

    def _preview_mouse_down(self, event):
        coords = self._image_coords_from_event(event)
        if coords is None:
            return
        x, y = coords
        if self._pick_color_from_preview(x, y):
            return
        layout = self._current_layout()
        target = self._resolve_drag_target(x, y, layout)
        if not target or target not in layout:
            return
        box = layout[target]
        cx = box["x"] + box["w"] / 2
        cy = box["y"] + box["h"] / 2
        self.drag_state = {"target": target, "dx": x - cx, "dy": y - cy}
        if target == "text":
            self.drag_state.update({
                "group_cx": cx,
                "group_cy": cy,
                "title_x": float(self.title_x_var.get()),
                "title_y": float(self.title_y_var.get()),
                "artist_x": float(self.artist_x_var.get()),
                "artist_y": float(self.artist_y_var.get()),
            })
        label = "Titel & Künstler" if target == "text" else target.capitalize()
        self.preview_status_var.set(f"{label} wird verschoben …")

    def _preview_mouse_move(self, event):
        if not self.drag_state:
            return
        coords = self._image_coords_from_event(event)
        if coords is None:
            return
        x, y = coords
        center_x = x - self.drag_state.get("dx", 0)
        center_y = y - self.drag_state.get("dy", 0)
        if self.drag_state.get("target") == "text" and (self.preview_composed_image is not None or self.preview_design_base_image is not None or self.preview_base_image is not None):
            canvas = self.preview_composed_image or self.preview_design_base_image or self.preview_base_image
            w, h = canvas.size
            dx_pct = (center_x - self.drag_state.get("group_cx", center_x)) * 100.0 / max(1, w)
            dy_pct = (center_y - self.drag_state.get("group_cy", center_y)) * 100.0 / max(1, h)
            self.title_x_var.set(int(round(max(0, min(100, self.drag_state.get("title_x", 50) + dx_pct)))))
            self.artist_x_var.set(int(round(max(0, min(100, self.drag_state.get("artist_x", 50) + dx_pct)))))
            self.title_y_var.set(int(round(max(0, min(110, self.drag_state.get("title_y", 82) + dy_pct)))))
            self.artist_y_var.set(int(round(max(0, min(110, self.drag_state.get("artist_y", 88) + dy_pct)))))
        else:
            self._set_center_pct(self.drag_state["target"], center_x, center_y)

    def _preview_mouse_up(self, event):
        if self.drag_state:
            target = self.drag_state.get("target", "Objekt")
            label = "Titel & Künstler" if target == "text" else str(target).capitalize()
            self.preview_status_var.set(f"{label} verschoben.")
        self.drag_state = None

    def start_render(self):
        if self.render_thread and self.render_thread.is_alive():
            return
        input_path = self.input_var.get().strip()
        output_path = self.output_var.get().strip()
        resolved_logo, resolved_cover = self._repair_asset_paths(allow_last=False)
        logo_path = resolved_logo or (self.logo_var.get().strip() or None)
        cover_path = resolved_cover or (self.cover_var.get().strip() or None)
        if not input_path:
            messagebox.showwarning(APP_TITLE, "Bitte zuerst ein Video auswählen.")
            return
        if not output_path:
            messagebox.showwarning(APP_TITLE, "Bitte eine Ausgabedatei wählen.")
            return
        if os.path.abspath(input_path) == os.path.abspath(output_path):
            messagebox.showwarning(APP_TITLE, "Die Ausgabedatei darf nicht identisch mit dem Original sein.")
            return

        if self.show_logo_var.get() and (not logo_path or not os.path.isfile(logo_path)):
            messagebox.showwarning(
                APP_TITLE,
                "Das Logo ist aktiviert, die Bilddatei konnte aber nicht mehr gefunden werden.\n\n"
                "Bitte das Logo einmal neu auswählen. Ab v1.9.5 wird es dauerhaft in der Anwendung gespeichert und danach automatisch wiedergefunden.",
            )
            return
        if self.show_cover_var.get() and (not cover_path or not os.path.isfile(cover_path)):
            messagebox.showwarning(
                APP_TITLE,
                "Das Cover ist aktiviert, die Bilddatei konnte aber nicht mehr gefunden werden.\n\n"
                "Bitte das Cover einmal neu auswählen. Ab v1.9.5 wird es dauerhaft in der Anwendung gespeichert und danach automatisch wiedergefunden.",
            )
            return

        settings = self._settings()
        for value in (settings.color_left, settings.color_mid, settings.color_right, settings.title_color, settings.artist_color, settings.cover_frame_color, settings.cover_glow_color):
            if not HEX_RE.match(value.strip()):
                messagebox.showwarning(APP_TITLE, f"Ungültige Farbe: {value}\nBitte #RRGGBB verwenden oder die Farbauswahl anklicken.")
                return

        self.progress_var.set(0)
        self.status_var.set("Vorbereitung …")
        self.render_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")

        def worker():
            try:
                self.renderer.render(
                    input_video=input_path,
                    output_video=output_path,
                    settings=settings,
                    logo_path=logo_path,
                    cover_path=cover_path,
                    progress_cb=self._progress_from_thread,
                )
            except Exception as exc:
                self.after(0, self._render_failed, str(exc))
            else:
                self.after(0, self._render_done, output_path)

        self.render_thread = threading.Thread(target=worker, daemon=True)
        self.render_thread.start()

    def _progress_from_thread(self, pct: float, text: str):
        self.after(0, self._set_progress, pct, text)

    def _set_progress(self, pct: float, text: str):
        self.progress_var.set(pct * 100)
        self.status_var.set(text)

    def _render_done(self, output_path: str):
        self.render_thread = None
        self.progress_var.set(100)
        self.status_var.set("Fertig")
        self.render_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        if messagebox.askyesno(APP_TITLE, f"Video wurde erstellt:\n\n{output_path}\n\nOrdner öffnen?"):
            self.open_folder(output_path)

    def _render_failed(self, message: str):
        self.render_thread = None
        self.status_var.set("Fehler")
        self.render_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        log_path = Path.home() / "AudioVisualizerStudio_last_error.txt"
        log_note = ""
        try:
            log_path.write_text(message, encoding="utf-8")
            log_note = f"\n\nFehlerdetails gespeichert unter:\n{log_path}"
        except Exception:
            pass
        visible = message.strip() or "Unbekannter Fehler"
        if len(visible) > 1800:
            visible = visible[-1800:]
        messagebox.showerror(APP_TITLE, visible + log_note)

    def cancel_render(self):
        self.renderer.cancel()
        self.status_var.set("Abbruch wird ausgeführt …")

    @staticmethod
    def open_folder(path: str):
        folder = str(Path(path).resolve().parent)
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{folder}"')
        else:
            os.system(f'xdg-open "{folder}" >/dev/null 2>&1 &')

    def _on_close(self):
        try:
            self.renderer.cancel()
            self.renderer.cancel_preview()
        except Exception:
            pass
        if self.frame_after_id is not None:
            try:
                self.after_cancel(self.frame_after_id)
            except tk.TclError:
                pass
        if self.redraw_after_id is not None:
            try:
                self.after_cancel(self.redraw_after_id)
            except tk.TclError:
                pass
        if self._history_after_id is not None:
            try:
                self.after_cancel(self._history_after_id)
            except tk.TclError:
                pass
        if self._autosave_after_id is not None:
            try:
                self.after_cancel(self._autosave_after_id)
            except tk.TclError:
                pass
        self._write_autosave()
        try:
            self._session_lock_file().unlink(missing_ok=True)
        except Exception:
            pass
        try:
            self.preview_temp.cleanup()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = VisualizerApp()
    app.mainloop()
