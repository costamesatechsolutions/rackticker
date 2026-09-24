import unittest
from PIL import Image
from app.core.fonts import text_width, draw_text, text_mask, normalize
from app.core.renderer import new_frame, clipped_text, scrolling_text, scroll_positions, transition, validate_frame


class PixelTests(unittest.TestCase):
    def test_a_smooth_d_keeps_its_square_side_so_it_is_not_an_o(self):
        d, o = text_mask("D", 2, True), text_mask("O", 2, True)
        self.assertEqual((d.getpixel((0, 0)), d.getpixel((0, d.height - 1))), (1, 1))
        self.assertEqual((o.getpixel((0, 0)), o.getpixel((0, o.height - 1))), (0, 0))

    def test_tiny_m_and_h_are_distinct_at_a_distance(self):
        from app.core.fonts import tiny_mask
        self.assertNotEqual(tiny_mask("M").size, tiny_mask("H").size)
        self.assertEqual(tiny_mask("19M").height, 5)

    def test_width_and_integer_scaling(self):
        self.assertEqual(text_width("ABC"), 17)
        self.assertEqual(text_width("12:34"), 27)
        self.assertEqual(text_width("ABC", 3), 51)
        self.assertEqual(text_width(""), 0)
        small = text_mask("AB", 1).convert("RGB")
        large = text_mask("AB", 3).convert("RGB")
        self.assertEqual(large.tobytes(), small.resize(large.size, Image.Resampling.NEAREST).tobytes())

    def test_normalization_and_original_binary_font(self):
        self.assertEqual(normalize("café — ok 😀"), "CAFE - OK ")
        self.assertEqual(normalize("“Why?” he said…"), "\"WHY?\" HE SAID...")
        frame = new_frame()
        draw_text(frame, "ABC 123", 1, 1, (50,100,200), 2)
        self.assertEqual({color for _, color in frame.getcolors()}, {(0,0,0), (50,100,200)})

    def test_negative_text_clipping(self):
        frame = new_frame()
        draw_text(frame, "AB", -4, -3)
        larger = Image.new("RGB", (150,50))
        draw_text(larger, "AB", 6, 7)
        self.assertEqual(frame.tobytes(), larger.crop((10,10,138,42)).tobytes())

    def test_scrolling_clips_to_requested_box(self):
        frame = Image.new("RGB", (128,32), (12,13,14))
        scrolling_text(frame, "A LONG SCROLLING MESSAGE", (20,10,40,7), 1.5, 12)
        for y in range(32):
            for x in range(128):
                if not (20 <= x < 60 and 10 <= y < 17):
                    self.assertEqual(frame.getpixel((x,y)), (12,13,14))

    def test_empty_clip_does_not_touch_frame(self):
        frame = new_frame()
        clipped_text(frame, "ABC", (0,0,0,7))
        self.assertIsNone(frame.getbbox())

    def test_scroll_starts_readable_wraps_and_has_gap(self):
        self.assertEqual(scroll_positions(200,128,0,24,32), (0,232))
        self.assertEqual(scroll_positions(200,128,1.5,24,32), (-36,196))
        self.assertEqual(scroll_positions(200,128,232/24,24,32), (0,232))
        self.assertEqual(scroll_positions(20,128,100,24,32), (0,))
        self.assertEqual(scroll_positions(200,128,-1,24,32), (0,232))
        positions = scroll_positions(200,128,3,24,32)
        self.assertEqual(positions[1] - (positions[0] + 200), 32)

    def test_transitions_have_exact_endpoints_and_no_interpolation(self):
        red = Image.new("RGB", (128,32), "red")
        blue = Image.new("RGB", (128,32), "blue")
        for kind in ("slide_left", "slide_up", "ticker"):
            self.assertEqual(transition(red, blue, 0, kind).tobytes(), red.tobytes())
            self.assertEqual(transition(red, blue, 1, kind).tobytes(), blue.tobytes())
            mid = transition(red, blue, .5, kind)
            self.assertEqual({color for _, color in mid.getcolors()}, {(255,0,0), (0,0,255)})
            self.assertEqual(mid.getpixel((0,0)), (255,0,0))
            self.assertEqual(mid.getpixel((127,31)), (0,0,255))
        self.assertIs(transition(red, blue, 0, "cut"), blue)

    def test_canonical_boundary_rejects_other_sizes_and_modes(self):
        for invalid in (Image.new("RGB", (256,64)), Image.new("RGBA", (128,32)), None):
            with self.assertRaises(ValueError): validate_frame(invalid)


if __name__ == "__main__": unittest.main()


class WrapTextTests(unittest.TestCase):
    def test_whole_words_that_fit(self):
        from app.core.fonts import text_width, wrap_text
        lines = wrap_text("Dodgers clinch the NL West with a walk-off homer", 126, mixed=True)
        self.assertEqual(" ".join(lines), "Dodgers clinch the NL West with a walk-off homer")
        self.assertTrue(all(text_width(line, 1, True) <= 126 for line in lines))

    def test_a_word_wider_than_a_line_is_split_not_lost(self):
        from app.core.fonts import text_width, wrap_text
        word = "https://example.com/an-extremely-long-address-that-never-ends"
        lines = wrap_text(word, 60)
        self.assertEqual("".join(lines), word.upper())
        self.assertTrue(all(text_width(line) <= 60 for line in lines))
        self.assertEqual(wrap_text("", 60), [])
