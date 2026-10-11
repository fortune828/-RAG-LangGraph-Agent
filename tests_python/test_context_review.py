"""Review exports must preserve human input and keep blind materials separate."""
import csv
import json
import tempfile
import unittest
from pathlib import Path

from evaluation.context_review import export_experience_pack, normalize_human_scores


class ContextReviewTests(unittest.TestCase):
    def test_normalize_keeps_original_bytes_and_does_not_invent_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'scores.csv'
            raw = ('\ufeff| 编号 | 理解 | 知识 | 关联 | 交流 | 帮助 | 是否回答到点上 | 理由或原句 | 评分人 | 日期 |\n'
                   '|---|---|---|---|---|---|---|---|---|---|\n'
                   '| R01 | 2 | 1 | 1 | 0 | 2 | 是 | 重复已有状态 | 人工 | 2026-10-11 |\n'
                   '| R02 | NA | NA | NA | NA | NA | 否 | 未交付 | 人工 | 2026-10-11 |\n').encode()
            source.write_bytes(raw)
            summary = normalize_human_scores(source, root/'normalized')
            self.assertEqual(source.read_bytes(), raw)
            self.assertEqual(summary['dimensions']['交流']['mean'], 0)
            self.assertEqual(summary['dimensions']['理解']['n'], 1)
            output = json.loads((root/'normalized/human-scores.json').read_text())
            self.assertFalse(output['rows'][0]['expertGold'])
            self.assertIsNone(output['rows'][0]['contextErrorTags'])
            self.assertEqual(output['rows'][0]['contextErrorTagsState'], 'not_collected')
            with self.assertRaises(FileExistsError):
                normalize_human_scores(source, root/'normalized')

    def test_anonymous_pack_preserves_history_answers_and_separates_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_answer = {'paragraphs': [{'text':'先前的完整回答', 'citations':[]} ]}
            answer = {'paragraphs':[{'text':'现在只回应新增条件。<script>', 'citations':[
                {'evidenceId':'e1', 'quote':'原文', 'claim':'新增条件'}]}]}
            cast = {'lines':[9,8,8,8,7,7], 'original':{'name':'益'}, 'changed':{'name':'观'}, 'changingIndices':[0]}
            rows = [dict(recordId='SECRET-MODEL-STRATEGY', groupId='internal-group', turnIndex=2,
                         question='更正之后呢', answer=answer, cast=cast,
                         inputSession={'question':'起初问题', 'turns':[{'question':'此前用户更正', 'answer':old_answer, 'trace':'PRIVATE TRACE'}]},
                         evidence=[{'id':'e1', 'text':'原文'}], errors=[], trace=[], status='complete')]
            result = export_experience_pack(rows, root/'human')
            self.assertEqual(result['count'], 1)
            for filename in ('materials.md', 'materials.html', 'materials.json', 'scores.csv', 'README.md'):
                content = (root/'human'/filename).read_text()
                self.assertNotIn('SECRET-MODEL-STRATEGY', content)
                self.assertNotIn('PRIVATE TRACE', content)
            materials = json.loads((root/'human/materials.json').read_text())
            self.assertEqual(materials[0]['answer'], answer)
            self.assertEqual(materials[0]['history'][0]['answer'], old_answer)
            self.assertIn('&lt;script&gt;', (root/'human/materials.html').read_text())
            with (root/'human/scores.csv').open(encoding='utf-8-sig') as handle:
                scores = list(csv.DictReader(handle))
            self.assertNotIn('知识', scores[0])
            self.assertTrue(all(not value for key, value in scores[0].items() if key != '编号'))
            self.assertTrue((root/'human-key.json').is_file())
            self.assertFalse((root/'human/key.json').exists())
            with self.assertRaises(FileExistsError):
                export_experience_pack(rows, root/'human')


if __name__ == '__main__':
    unittest.main()
