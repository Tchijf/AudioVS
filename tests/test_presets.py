import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

if "PIL" not in sys.modules and importlib.util.find_spec("PIL") is None:
    pil_module = types.ModuleType("PIL")
    for module_name in ("Image", "ImageDraw", "ImageFilter", "ImageFont", "ImageOps", "ImageTk"):
        submodule = types.ModuleType(f"PIL.{module_name}")
        setattr(pil_module, module_name, submodule)
        sys.modules[f"PIL.{module_name}"] = submodule
    sys.modules["PIL"] = pil_module

import app


class PresetContentTests(unittest.TestCase):
    def test_design_state_keeps_artist_and_title(self):
        fake_app = types.SimpleNamespace(
            _snapshot_state=lambda: {
                "artist": "Test Artist",
                "title": "Test Title",
                "visualizer_style": "Klassische Balken",
            }
        )

        state = app.VisualizerApp._preset_design_state(fake_app)

        self.assertEqual("Test Artist", state.get("artist"))
        self.assertEqual("Test Title", state.get("title"))

    def test_asset_copy_never_overwrites_the_active_legacy_file(self):
        copy_asset = getattr(app, "_copy_preset_asset", None)
        self.assertIsNotNone(copy_asset, "Preset asset copying must use the safe public helper")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "selected-cover.png"
            source.write_bytes(b"new cover content")
            assets_dir = root / "preset_assets"
            assets_dir.mkdir()
            active_legacy_file = assets_dir / "My_Preset_cover.png"
            active_legacy_file.write_bytes(b"currently in use")

            first = Path(copy_asset(str(source), assets_dir, "My_Preset", "cover"))
            second = Path(copy_asset(str(source), assets_dir, "My_Preset", "cover"))

            self.assertNotEqual(active_legacy_file, first)
            self.assertEqual(b"currently in use", active_legacy_file.read_bytes())
            self.assertEqual(b"new cover content", first.read_bytes())
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

