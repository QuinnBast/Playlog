import unittest

from playtime_agent import vdf


class VdfTest(unittest.TestCase):
    def test_nested_blocks_and_values(self):
        data = vdf.loads('"Root"\n{\n\t"a"\t\t"1"\n\t"child"\n\t{\n\t\t"b"\t"two words"\n\t}\n}\n')
        self.assertEqual(data, {"Root": {"a": "1", "child": {"b": "two words"}}})

    def test_escapes_comments_conditionals_and_bom(self):
        text = '\ufeff// comment\n"k"\n{\n "path" "C:\\\\Steam" [$WIN32]\n "q" "say \\"hi\\""\n}'
        self.assertEqual(vdf.loads(text), {"k": {"path": "C:\\Steam", "q": 'say "hi"'}})

    def test_unquoted_tokens(self):
        self.assertEqual(vdf.loads("root { key value }"), {"root": {"key": "value"}})

    def test_repeated_blocks_merge(self):
        self.assertEqual(vdf.loads('"a" { "x" "1" } "a" { "y" "2" }'), {"a": {"x": "1", "y": "2"}})

    def test_get_path_ignores_case(self):
        data = {"Software": {"valve": {"steam": {"apps": {"1": {}}}}}}
        self.assertEqual(vdf.get_path(data, "software", "Valve", "Steam", "apps"), {"1": {}})
        self.assertIsNone(vdf.get_path(data, "Software", "Missing"))
        self.assertIsNone(vdf.get_path({"a": "text"}, "a", "b"))

    def test_malformed(self):
        for text in ['"a" {', '"a" }', '"a" "unterminated', '{ }']:
            with self.subTest(text=text), self.assertRaises(vdf.VdfError):
                vdf.loads(text)


if __name__ == "__main__":
    unittest.main()
