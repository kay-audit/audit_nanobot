"""Offline behavioral regression tests; runnable with unittest or pytest."""
from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from application.history import Attachment, analyze, inventory
from document.history import NS, parse_overview, read_lines

LAW = "Федеральный закон от 13.03.2006 N 38-ФЗ"
TARGET = LAW + " (ред. от 04.08.2026).docx"
REVIEW = "Обзор изменений Федерального закона от 13.03.2006 N 38-ФЗ.docx"
BAR = " ─" + "─" * 72
HEADER = "          старая редакция                       новая редакция"


def docx(lines):
    stream = BytesIO()
    xml = '<w:document xmlns:w="' + NS[1:-1] + '"><w:body>'
    xml += ''.join('<w:p><w:r><w:t xml:space="preserve">' + escape(line) + '</w:t></w:r></w:p>' for line in lines)
    xml += '</w:body></w:document>'
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return stream.getvalue()


def sample():
    return [REVIEW[:-5], "Редакция от 04.08.2026",
            "(с изм. и доп., вступ. в силу с 01.03.2027)",
            "Редакция подготовлена на основе изменений, внесенных законом от 01.01.2026 N 1-ФЗ.",
            BAR, "    Изменение части 1 статьи 5", "", HEADER, "",
            "    Срок 10 дней".ljust(40) + "Срок 20 дней", BAR,
            "* * *", "Редакция от 01.01.2020", BAR,
            "Дополнение статьи 5 частью 2. См. текст новой редакции",
            "2. Не допускается реклама.", BAR]


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / TARGET).write_bytes(docx([TARGET[:-5], "Список изменяющих документов", "Статья 5. Реклама"]))
        (self.root / REVIEW).write_bytes(docx(sample()))

    def tearDown(self):
        self.temp.cleanup()

    def run_history(self, **kwargs):
        return analyze([str(self.root)], target=TARGET, **kwargs)

    def test_missing_target_does_not_read_any_docx(self):
        with patch.object(Attachment, "read", side_effect=AssertionError("must not read")):
            result = analyze([str(self.root)])
        self.assertEqual(result["status"], "needs_input")
        self.assertIn("Все ли", result["message"])

    def test_two_columns_dates_and_chronology(self):
        events, _ = parse_overview(sample())
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["revision_date"], "2020-01-01")
        self.assertIsNone(events[0]["effective_date"])
        change = events[1]
        self.assertEqual(change["before"], "Срок 10 дней")
        self.assertEqual(change["after"], "Срок 20 дней")
        self.assertEqual(change["text_changes"], [{"before": "10", "after": "20"}])
        self.assertEqual(change["revision_date"], "2026-08-04")
        self.assertEqual(change["effective_date"], "2027-03-01")

    def test_only_pair_is_read(self):
        (self.root / "Федеральный закон от 01.01.2020 N 3-ФЗ.docx").write_bytes(b"invalid")
        original = Attachment.read
        seen = []
        def reader(item):
            seen.append(item.name)
            return original(item)
        with patch.object(Attachment, "read", reader):
            result = self.run_history()
        self.assertEqual(result["status"], "ok")
        self.assertEqual(seen, [TARGET, REVIEW])

    def test_latest_selected_without_reading_old(self):
        old = TARGET.replace("04.08.2026", "01.01.2020")
        (self.root / old).write_bytes(b"intentionally unreadable")
        result = analyze([str(self.root)], target=old)
        self.assertEqual(result["target"], str(self.root / TARGET))

    def test_mismatched_overview_content(self):
        lines = sample()
        lines[0] = lines[0].replace("38-ФЗ", "353-ФЗ")
        (self.root / REVIEW).write_bytes(docx(lines))
        self.assertEqual(self.run_history()["status"], "needs_input")

    def test_missing_overview_does_not_fallback(self):
        (self.root / REVIEW).unlink()
        self.assertEqual(self.run_history()["status"], "needs_input")

    def test_ambiguous_overview_requires_selection(self):
        other = REVIEW.replace(".docx", " копия.docx")
        (self.root / other).write_bytes(docx(sample()))
        self.assertEqual(self.run_history()["status"], "needs_input")
        self.assertEqual(self.run_history(overview=other)["status"], "ok")

    def test_pagination_preserves_all_events(self):
        first = self.run_history(limit=1)
        second = self.run_history(offset=first["next_offset"], limit=1)
        self.assertIsNone(second["next_offset"])
        self.assertEqual(first["total_changes"], 2)
        self.assertNotEqual(first["changes"][0]["id"], second["changes"][0]["id"])

    def test_current_context_not_substituted_for_missing_history(self):
        lines = sample()
        lines[9] = "В связи с большим объемом текст не приводится."
        (self.root / REVIEW).write_bytes(docx(lines))
        result = self.run_history()
        change = result["changes"][1]
        self.assertIsNone(change["before"])
        self.assertIsNone(change["after"])
        self.assertEqual(change["text_changes"], [])
        self.assertTrue(self.run_history(field="current")["current_blocks"])

    def test_deletion_and_addition(self):
        lines = ["Редакция от 01.01.2020", BAR,
                 "Часть 1 статьи 5 исключена. См. текст старой редакции", "1. Старый запрет.", BAR]
        events, _ = parse_overview(lines)
        self.assertEqual(events[0]["before"], "1. Старый запрет.")
        self.assertEqual(events[0]["after"], "Исключено")
        self.assertEqual(parse_overview(sample())[0][0]["after"], "2. Не допускается реклама.")

    def test_same_revision_different_effective_dates_preserved(self):
        other = sample()[1:11]
        other[1] = "(с изм. и доп., вступ. в силу с 01.09.2026)"
        events, _ = parse_overview(sample() + other)
        self.assertEqual(len(events), 3)
        self.assertEqual([e["effective_date"] for e in events[1:]], ["2026-09-01", "2027-03-01"])

    def test_zip_pair_and_no_extraction(self):
        path = self.root / "bundle.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("nested/" + TARGET, (self.root / TARGET).read_bytes())
            archive.writestr("nested/" + REVIEW, (self.root / REVIEW).read_bytes())
            archive.writestr("unrelated.docx", b"bad docx")
        result = analyze([str(path)], target=TARGET)
        self.assertEqual(result["total_changes"], 2)
        self.assertFalse((self.root / "nested").exists())

    def test_unsafe_zip_member(self):
        path = self.root / "bad.zip"
        with ZipFile(path, "w") as archive:
            archive.writestr("../evil.docx", b"bad")
        with self.assertRaises(ValueError):
            inventory([str(path)])

    def test_bad_docx_structured_cli_error(self):
        (self.root / TARGET).write_bytes(b"bad")
        process = subprocess.run([sys.executable, str(SCRIPTS / "cli_history.py"), "--raw", "--input", str(self.root), "--target", TARGET], capture_output=True)
        self.assertEqual(process.returncode, 1)
        self.assertEqual(json.loads(process.stdout)["status"], "error")

    def test_cli_from_other_cwd_and_unicode_paths(self):
        process = subprocess.run([sys.executable, str(SCRIPTS / "cli_history.py"), "--raw", "--input", str(self.root), "--target", TARGET], cwd=self.root, capture_output=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["total_changes"], 2)

    def test_invalid_pagination(self):
        with self.assertRaises(ValueError):
            self.run_history(limit=0)

    def test_broken_columns_do_not_invent_text(self):
        lines = sample()
        lines[9] = "X" * 80
        events, _ = parse_overview(lines)
        self.assertIsNone(events[1]["before"])
        self.assertIn("X" * 80, events[1]["source_text"])

    def test_xml_dtd_rejected(self):
        stream = BytesIO()
        with ZipFile(stream, "w") as archive:
            archive.writestr("word/document.xml", '<!DOCTYPE x [<!ENTITY a "x">]><x/>')
        with self.assertRaises(ValueError):
            read_lines(stream.getvalue())


class RealDocumentsTests(unittest.TestCase):
    def verify(self, folder, expected, comparisons):
        fixtures = Path(os.environ.get("DOC_HISTORY_FIXTURES", str(SCRIPTS.parents[4] / "Consultant"))) / folder
        if not fixtures.is_dir():
            self.skipTest("Set DOC_HISTORY_FIXTURES to the supplied Consultant directory")
        target = next(p for p in fixtures.glob("*.docx") if "(ред. от 04.08.2026)" in p.name)
        review = next(fixtures.glob("Обзор*"))
        lines = read_lines(review.read_bytes())
        events, _ = parse_overview(lines)
        self.assertEqual(len(events), expected)
        self.assertEqual(sum("старая редакция" in line for line in lines), comparisons)
        self.assertEqual([e["sort_date"] for e in events], sorted(e["sort_date"] for e in events))
        self.assertEqual(len({e["id"] for e in events}), expected)
        self.assertTrue(any(e["text_changes"] for e in events))
        self.assertTrue(any(e["after"] is None for e in events))
        self.assertFalse(any("Нераспознан" in m for e in events for m in e["missing"]))
        original = Attachment.read
        seen = []
        def reader(item):
            seen.append(item.name)
            return original(item)
        with patch.object(Attachment, "read", reader):
            result = analyze([str(fixtures)], target=target.name)
        self.assertEqual(result["total_changes"], expected)
        self.assertEqual(seen, [target.name, review.name])
        self.assertTrue(result["amending_documents"])
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "документы.zip"
            with ZipFile(bundle, "w") as archive:
                archive.write(target, "вложения/" + target.name)
                archive.write(review, "вложения/" + review.name)
                archive.writestr("неиспользуемый.docx", b"must not be parsed")
            zipped = analyze([str(bundle)], target=target.name)
            self.assertEqual(zipped["total_changes"], expected)
            self.assertEqual(zipped["changes"], result["changes"])
        if folder == "353fz":
            latest = next(e for e in events if e["revision_date"] == "2026-08-04" and e["provision"] == "Изменение пункта 2 части 1 статьи 6.1-1")
            self.assertIn("пунктами 1 - 5 части", latest["before"])
            self.assertIn("пунктами 1 - 5 и 7 части", latest["after"])
        else:
            latest = next(e for e in events if e["effective_date"] == "2027-03-01" and e["provision"].startswith("Часть 10.3 статьи 5"))
            self.assertNotIn("федеральной почтовой связи", latest["before"])
            self.assertIn("федеральной почтовой связи", latest["after"])

    def test_38fz(self):
        self.verify("38fz", 368, 259)

    def test_353fz(self):
        self.verify("353fz", 179, 112)


if __name__ == "__main__":
    unittest.main()
