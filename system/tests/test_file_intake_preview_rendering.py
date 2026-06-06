import unittest

from app.file_intake import artifact_kind_for_file, extract_preview_from_bytes, guess_mime


class FileIntakePreviewRenderingTests(unittest.TestCase):
    def test_md_files_are_markdown_reports(self) -> None:
        content = b"# Draft\n\n| Item | Status |\n| --- | --- |\n| UI | Ready |\n"

        preview = extract_preview_from_bytes(content, filename="latest-work.md")

        self.assertEqual(guess_mime("latest-work.md"), "text/markdown")
        self.assertEqual(guess_mime("latest-work.md", "application/octet-stream"), "text/markdown")
        self.assertEqual(artifact_kind_for_file("latest-work.md", "text/markdown"), "report")
        self.assertEqual(preview.preview_kind, "md")
        self.assertIn("| Item | Status |", preview.text)

    def test_csv_preview_is_gfm_table(self) -> None:
        preview = extract_preview_from_bytes(
            b"Name,Score\nAlpha,10\nBeta,8\n",
            filename="scores.csv",
            mime="text/csv",
        )

        self.assertEqual(preview.preview_kind, "sheet")
        self.assertEqual(
            preview.text.splitlines()[:4],
            [
                "| Name | Score |",
                "| --- | --- |",
                "| Alpha | 10 |",
                "| Beta | 8 |",
            ],
        )
