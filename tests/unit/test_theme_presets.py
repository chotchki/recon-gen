"""Tests for the theme preset system.

Per N.4.l, the lookup-by-name registry was dropped — `DEFAULT_PRESET`
is the single fallback preset (used when an L2 instance omits its
inline ``theme:`` block). All other brand palettes live inline on
each L2 YAML's ``theme:`` block.

The QS ``Theme`` resource builder (``build_theme``) + its serialization
tests retired with the QS emitter (DW phase) — what survives is the
``DEFAULT_PRESET`` palette the renderers resolve colors from.
"""

from recon_gen.common.theme import DEFAULT_PRESET, _DARK_BLUE


class TestDefaultPreset:
    def test_name(self):
        assert DEFAULT_PRESET.theme_name == "Recon Gen Theme"

    def test_no_analysis_prefix(self):
        assert DEFAULT_PRESET.analysis_name_prefix is None

    def test_accent_is_blue(self):
        assert DEFAULT_PRESET.accent == _DARK_BLUE

    def test_eight_data_colors(self):
        assert len(DEFAULT_PRESET.data_colors) == 8
