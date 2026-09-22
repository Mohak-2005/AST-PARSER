import unittest
from app.services.filename_sanitizer import (
    sanitize_test_title_to_filename,
    resolve_unique_script_filename,
)


class TestFilenameSanitizer(unittest.TestCase):
    def test_basic_sanitization(self):
        title = "Render ActiveUsers component with empty user list"
        res = sanitize_test_title_to_filename(title)
        self.assertEqual(res, "Render ActiveUsers component with empty user list")

    def test_illegal_characters_stripped(self):
        title = "Render User: Active/Inactive state? <Check* \"quoted\" | pipe>"
        res = sanitize_test_title_to_filename(title)
        self.assertEqual(res, "Render User Active Inactive state Check quoted pipe")
        for bad_char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            self.assertNotIn(bad_char, res)

    def test_whitespace_normalization(self):
        title = "  Multiple    spaces   and\tnewlines\n "
        res = sanitize_test_title_to_filename(title)
        self.assertEqual(res, "Multiple spaces and newlines")

    def test_empty_fallback(self):
        self.assertEqual(sanitize_test_title_to_filename(""), "untitled_test")
        self.assertEqual(sanitize_test_title_to_filename(":::???"), "untitled_test")
        self.assertEqual(sanitize_test_title_to_filename(None), "untitled_test")

    def test_word_boundary_truncation(self):
        long_title = "This is a very long title that should be truncated cleanly at word boundaries without cutting a single word into parts unnecessarily"
        res = sanitize_test_title_to_filename(long_title, max_length=50)
        self.assertLessEqual(len(res), 50)
        self.assertFalse(res.endswith(" "))
        self.assertTrue(long_title.startswith(res))

    def test_collision_resolution(self):
        title = "Render ActiveUsers component"
        ext = ".js"
        existing = {"Render ActiveUsers component.js"}

        # First case (no collision)
        res1 = resolve_unique_script_filename(title, "TC-001", ext, set())
        self.assertEqual(res1, "Render ActiveUsers component.js")

        # Second case with identical title (collision)
        res2 = resolve_unique_script_filename(title, "TC-002", ext, existing)
        self.assertEqual(res2, "Render ActiveUsers component__TC-002.js")


if __name__ == "__main__":
    unittest.main()
