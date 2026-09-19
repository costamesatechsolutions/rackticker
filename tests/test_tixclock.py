import unittest
from datetime import datetime, timezone

from app.core.config import validate_config
from app.core.models import Snapshot
from app.core.renderer import validate_frame
from app.modules.base import RenderContext
from app.modules.tixclock import GROUP_GRIDS, TixClockModule, lit_positions, tix_digits


class TixClockTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 12, 15, 24, tzinfo=timezone.utc)
        self.config = validate_config({})
        self.context = RenderContext(self.now, 0, self.config, {}, None, None)
        self.module = TixClockModule()

    def test_digits_support_12_and_24_hour_modes(self):
        self.assertEqual(tix_digits(self.now, "12"), (0, 3, 2, 4))
        self.assertEqual(tix_digits(self.now, "24"), (1, 5, 2, 4))
        midnight = self.now.replace(hour=0, minute=7)
        self.assertEqual(tix_digits(midnight, "12"), (1, 2, 0, 7))
        self.assertEqual(tix_digits(midnight, "24"), (0, 0, 0, 7))

    def test_each_group_lights_exactly_the_digit_and_is_stable_per_bucket(self):
        self.assertEqual(tuple(len(grid) for grid in GROUP_GRIDS), (3, 9, 6, 9))
        for group, grid in enumerate(GROUP_GRIDS):
            for digit in range(len(grid) + 1):
                first = lit_positions(digit, 123, group)
                second = lit_positions(digit, 123, group)
                self.assertEqual(len(first), digit)
                self.assertEqual(first, second)
                self.assertTrue(first <= set(grid))
        self.assertNotEqual(lit_positions(2, 123, 0), lit_positions(2, 124, 0))
        self.assertNotEqual(lit_positions(5, 123, 1), lit_positions(5, 123, 3))
        with self.assertRaises(ValueError):
            lit_positions(4, 123, 0)

    def test_patterns_never_repeat_back_to_back_and_full_fields_stay_solid(self):
        for group, digit in ((0, 1), (0, 2), (1, 5), (2, 3), (3, 8)):
            sequence = [lit_positions(digit, bucket, group) for bucket in range(300)]
            self.assertFalse(any(a == b for a, b in zip(sequence, sequence[1:])), (group, digit))
        self.assertEqual({lit_positions(9, bucket, 1) for bucket in range(20)}, {frozenset(GROUP_GRIDS[1])})

    def test_render_is_canonical_and_updates_on_configured_interval(self):
        frame = self.module.render(self.context)
        validate_frame(frame)
        self.assertEqual(frame.mode, "RGB")
        self.assertIsNotNone(frame.getbbox())
        self.config["modules"]["tixclock"]["update_interval"] = 1
        first = self.module.render(self.context)
        later_context = RenderContext(self.now.replace(second=1), 0, self.config, {}, None, None)
        second = self.module.render(later_context)
        self.assertNotEqual(first.tobytes(), second.tobytes())

    def test_unlabelled_layout_uses_the_extra_height(self):
        without_label = self.module.render(self.context)
        self.config["modules"]["tixclock"]["show_label"] = True
        with_label = self.module.render(self.context)
        self.assertNotEqual(with_label.tobytes(), without_label.tobytes())
        self.assertIsNotNone(without_label.crop((0, 27, 128, 30)).getbbox())
        lit_without = sum(count for count, color in without_label.getcolors(128 * 32) if color != (0, 0, 0))
        lit_with = sum(count for count, color in with_label.getcolors(128 * 32) if color != (0, 0, 0))
        self.assertGreater(lit_without, lit_with)

    def test_unlit_original_size_hour_tens_field_remains_visible(self):
        frame = self.module.render(self.context)
        # At 03:24 the first 1×3 field has no lit cells, but its three lenses
        # remain faintly visible. The hour tens field is not stretched to 3×3.
        for y in (4, 12, 20):
            self.assertNotEqual(frame.getpixel((13, y)), (0, 0, 0))
        self.assertEqual(frame.getpixel((22, 4)), (0, 0, 0))


if __name__ == "__main__": unittest.main()
