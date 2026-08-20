import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


if importlib.util.find_spec("PIL") is None:
    pil_module = types.ModuleType("PIL")
    for module_name in ("Image", "ImageDraw", "ImageFilter", "ImageFont", "ImageOps", "ImageTk"):
        submodule = types.ModuleType(f"PIL.{module_name}")
        setattr(pil_module, module_name, submodule)
        sys.modules[f"PIL.{module_name}"] = submodule
    sys.modules["PIL"] = pil_module

import app


class HeadlessVisualizerApp(app.VisualizerApp):
    """Run the real constructor while replacing GUI and filesystem boundaries."""

    session_directory: Path

    def title(self, *_args):
        pass

    def geometry(self, *_args):
        pass

    def minsize(self, *_args):
        pass

    def configure(self, **_kwargs):
        pass

    def _read_app_settings(self):
        return {}

    def _configure_style(self):
        pass

    def _set_window_icon(self):
        pass

    def _session_lock_file(self):
        return self.session_directory / "session.lock"

    def _build_vars(self):
        pass

    def _build_ui(self):
        pass

    def after_idle(self, *_args):
        pass

    def after(self, *_args):
        pass

    def _load_presets_into_ui(self):
        pass

    def _bind_preview_updates(self):
        pass

    def _update_color_swatches(self):
        pass

    def _update_ffmpeg_status(self):
        pass

    def _apply_theme_to_raw_widgets(self):
        pass

    def _snapshot_state(self):
        return {}

    def bind_all(self, *_args):
        pass

    def _schedule_autosave(self):
        pass

    def protocol(self, *_args):
        pass


class VisualizerAppStartupTests(unittest.TestCase):
    def test_constructor_completes_without_missing_callback(self):
        with tempfile.TemporaryDirectory() as directory:
            HeadlessVisualizerApp.session_directory = Path(directory)
            with mock.patch.object(app.tk.Tk, "__init__", return_value=None):
                instance = HeadlessVisualizerApp()
            instance.preview_temp.cleanup()


if __name__ == "__main__":
    unittest.main()

