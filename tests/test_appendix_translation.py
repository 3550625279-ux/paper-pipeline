import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
sys.path.insert(0, str(APP / 'vendor'))
import worker_translate as worker
import quality_check
import detector


def make_pdf(path, layouts):
    doc = fitz.open()
    for lines in layouts:
        page = doc.new_page()
        y = 70
        for heading, text in lines:
            page.insert_text((72, y), text, fontsize=15 if heading else 10,
                             fontname='hebo' if heading else 'helv')
            y += 24
    doc.save(str(path))
    doc.close()


def selected_pages(spec, count):
    if spec is None:
        return set(range(1, count + 1))
    pages = set()
    for component in spec.split(','):
        bounds = component.strip().split('-')
        start = int(bounds[0])
        end = int(bounds[-1])
        pages.update(range(start, end + 1))
    return pages


class AppendixTranslationTest(unittest.TestCase):
    def run_fixture(self, layouts, stop=True, retry=False, source_pdf=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'source.pdf'
            if source_pdf is None:
                make_pdf(source, layouts)
                page_count = len(layouts)
            else:
                source = Path(source_pdf)
                with fitz.open(source) as doc:
                    page_count = len(doc)
            captures, checked, events = [], [], []
            async def fake_translate(settings, pdf, label):
                pass
            def fake_settings(pdf, out_dir, cfg, **kwargs):
                captures.append(kwargs.get('pages'))
                return object()
            def fake_check(pdf, pages_to_check):
                checked.append(set(pages_to_check))
                return [{'page': 1}] if retry and len(checked) == 1 else []
            with patch.object(worker, 'build_settings', side_effect=fake_settings), \
                 patch.object(worker, 'stream_translate', side_effect=fake_translate), \
                 patch.object(worker, 'collect_outputs', return_value={'mono': root / 'mono.pdf'}), \
                 patch.object(worker, 'emit', side_effect=events.append), \
                 patch.object(quality_check, 'check_untranslated_prose', side_effect=fake_check):
                worker.run_job({'translatorConfig': {'stop_at_references': stop},
                                'workDir': str(root / 'job'), 'sourcePdf': str(source)})
            return [selected_pages(s, page_count) for s in captures], checked, events

    def test_appendix_after_multi_page_references_and_retry(self):
        layouts = [
            [(True, 'Introduction'), (False, 'This study investigates a simple problem.')],
            [(True, 'Methods'), (False, 'We describe our experimental protocol.')],
            [(True, 'References'), (False, '[1] Smith et al. 2020 Journal'), (False, '[2] Jones 2021 Proceedings')],
            [(False, '[3] Taylor et al. 2022 Conference')],
            [(False, '[4] Miller et al. 2023 Proceedings')],
            [(True, 'Appendix A'), (False, 'This appendix describes the experimental setup in detail.')],
            [(True, 'Appendix B'), (False, 'Additional results are presented in this section.')],
        ]
        translated, checked, events = self.run_fixture(layouts, retry=True)
        self.assertEqual(translated, [{1, 2, 3, 6, 7}] * 2)
        self.assertEqual(checked, [{1, 2, 3, 6, 7}] * 2)
        self.assertEqual(next(e for e in events if e["type"] == "done")["translatedPages"], 5)

    def test_appendix_sharing_reference_page(self):
        layouts = [
            [(True, 'Introduction'), (False, 'We evaluate our method.')],
            [(True, 'Methods'), (False, 'Our method uses standard techniques.')],
            [(True, 'References'), (False, '[1] Smith et al. 2020 Journal'), (False, '[2] Jones 2021 Proceedings')],
            [(False, '[3] Taylor et al. 2022 Conference'), (True, 'Appendix A'), (False, 'A detailed analysis follows.')],
            [(False, 'This is another page of appendix prose.')],
        ]
        translated, checked, events = self.run_fixture(layouts)
        self.assertEqual(translated, [{1, 2, 3, 4, 5}])
        self.assertEqual(checked, [{1, 2, 3, 4, 5}])

    def test_without_recognizable_appendix_translates_all_pages(self):
        layouts = [
            [(True, 'Introduction'), (False, 'The research question is introduced here.')],
            [(True, 'Methods'), (False, 'We describe the experimental method.')],
            [(True, 'References'), (False, '[1] Smith et al. 2020 Journal'), (False, '[2] Jones 2021 Proceedings')],
            [(False, '[3] Taylor et al. 2022 Conference')],
        ]
        translated, checked, events = self.run_fixture(layouts)
        self.assertEqual(translated, [{1, 2, 3, 4}])
        self.assertEqual(checked, [{1, 2, 3, 4}])


    def test_disabled_reference_detection_translates_all_pages(self):
        layouts = [
            [(True, 'Introduction'), (False, 'The research question is introduced here.')],
            [(True, 'References'), (False, '[1] Smith et al. 2020 Journal')],
            [(True, 'Appendix A'), (False, 'Appendix details are presented here.')],
        ]
        translated, checked, events = self.run_fixture(layouts, stop=False)
        self.assertEqual(translated, [{1, 2, 3}])

    def test_split_letter_heading_after_references(self):
        # ACL papers may put the letter and title into separate PDF text blocks.
        # Create that layout without depending on a user-specific local PDF.
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'paper.pdf'
            make_pdf(source, [
                [(True, 'Introduction'), (False, 'The research question is introduced here.')],
                [(True, 'Methods'), (False, 'We describe the experimental method.')],
                [(True, 'References'), (False, '[1] Smith et al. 2020 Journal'),
                 (False, '[2] Jones 2021 Proceedings')],
                [(False, '[3] Taylor et al. 2022 Conference')],
                [], [],
            ])
            doc = fitz.open(str(source))
            for page, letter, title in [(doc[4], 'A', 'Related Work'),
                                        (doc[5], 'B', 'Detailed Experiments')]:
                page.insert_text((72, 70), letter, fontsize=15, fontname='hebo')
                page.insert_text((95, 70), title, fontsize=15, fontname='hebo')
                page.insert_text((72, 110), 'Additional prose about the research.', fontsize=10)
            updated = Path(tmp) / 'updated.pdf'
            doc.save(str(updated))
            doc.close()
            self.assertEqual(detector.detect_reference_start(str(updated)), 3)
            self.assertEqual(detector.detect_appendix_after_references(str(updated), 3), 5)
            translated, checked, events = self.run_fixture([], source_pdf=updated)
            self.assertEqual(translated, [{1, 2, 3, 5, 6}])
            self.assertEqual(checked, [{1, 2, 3, 5, 6}])

if __name__ == '__main__':
    unittest.main()
