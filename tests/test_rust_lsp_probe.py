"""Protocol/range rejection checks for the disposable LSP feasibility probe."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "rust_lsp_probe", Path(__file__).resolve().parents[1] / "scripts/rust_lsp_probe.py")
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class ProbeTests(unittest.TestCase):
    def test_utf16_offsets_preserve_unicode_and_crlf(self):
        source = "🚀é\r\naccount\r\n"
        self.assertEqual(probe.byte_offset(source, {"line": 0, "character": 2}), 4)
        self.assertEqual(probe.byte_offset(source, {"line": 0, "character": 3}), 6)
        self.assertEqual(probe.byte_offset(source, {"line": 1, "character": 0}), 8)
        self.assertEqual(probe.byte_offset(source, {"line": 2, "character": 0}), 17)
        for line, character in [(0, 1), (0, 4), (1, 8), (3, 0), (-1, 0), (0, -1), (True, 0)]:
            with self.assertRaises(AssertionError):
                probe.byte_offset(source, {"line": line, "character": character})

    def test_result_paths_and_ranges_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root / "main.rs").write_text("account")
            span = {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 7}}
            valid = {"uri": (root / "main.rs").as_uri(), "range": span}
            self.assertEqual(probe.scoped_ranges(root, "account", [valid]), [(0, 7)])
            (root / "alias.rs").symlink_to(root / "main.rs")
            for uri in ["https://example.com/main.rs", "file://remote/main.rs",
                        (root.parent / "outside.rs").as_uri(), (root / "alias.rs").as_uri(),
                        (root / ".ffsake" / "main.rs").as_uri(), valid["uri"] + "?query",
                        root.as_uri() + "/../main.rs"]:
                with self.assertRaises((AssertionError, ValueError)):
                    probe.scoped_ranges(root, "account", [{**valid, "uri": uri}])
            with self.assertRaises(AssertionError):
                probe.scoped_ranges(root, "changed", [valid])
            with self.assertRaises(AssertionError):
                probe.scoped_ranges(root, "account", [valid] * 101)

    def test_frame_limits_and_duplicate_headers(self):
        for frame in [b"Content-Length: 1048577\r\n\r\n", b"Content-Length: -1\r\n\r\n",
                      b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}", b"x" * 8193]:
            client = probe.ProbeClient.__new__(probe.ProbeClient)
            client.buffer = bytearray(frame)
            with self.assertRaises(AssertionError):
                client.read()
        client = probe.ProbeClient.__new__(probe.ProbeClient)
        payload = json.dumps({"result": "é"}).encode()
        client.buffer = bytearray(f"content-length: {len(payload)}\r\n\r\n".encode() + payload)
        self.assertEqual(client.read(), {"result": "é"})
        self.assertEqual(client.buffer, b"")


if __name__ == "__main__":
    unittest.main()
