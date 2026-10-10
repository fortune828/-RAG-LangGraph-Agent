"""Attach explicit, reversible adjudications to read-only classic collation.

Run review-classics.py first. No source or runtime corpus is changed.
"""
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/knowledge/review/crosscheck-20260926/classic'
rows = [json.loads(s) for s in (OUT / 'records.jsonl').read_text().splitlines()]
additional = {s['hexagram_id']: s for s in json.loads((OUT / 'additional-sources.json').read_text())}
for s in additional.values():
    assert hashlib.sha256((OUT / s['raw_file']).read_bytes()).hexdigest() == s['sha256']

# Exact, minimal amendments; supporting quotations are classical public-domain
# text only. These are recommendations, never an in-place release rewrite.
fixes = {
    'zy-6-line_xiang-1': ('“有小言”', '“小有言”', '雖小有言，其辯明也', '词序误置'),
    'zy-7-line_xiang-5': ('弟子舆师', '弟子舆尸', '弟子輿尸，使不當也', '师/尸误字'),
    'zy-31-line_xiang-5': ('志未也', '志末也', '咸其脢，志末也', '未/末形近误字'),
    'zy-35-line_xiang-1': ('晋中摧如', '晋如摧如', '晉如摧如，獨行正也', '中/如误字'),
    'zy-55-tuan': ('而况人于人乎', '而况于人乎', '而況於人乎', '衍入一个人字'),
}
notes = {
    'zy-1-tuan': '大和/太和保留字词异文；同一周易正义页面经文作大和、注文作太和，不据多数录文改库。',
    'zy-5-line_xiang-2': '易学网周易正义《象》全文支持现文衍在中也、以吉终也；Kanripo行/衍与维基终吉/吉终不用于自动改库。',
    'zy-14-line_xiang-4': '辨/辯、晢/晰在各录文间不一致；未确定当前底本，保留待核，不合并成一个所谓唯一原文。',
    'zy-22-tuan': '刚柔交错涉及校补：周易正义正文仅天文也，王注另有刚柔交错而成文焉。现文有版本支持，不能把不含校补四字判作采集遗漏。',
    'zy-44-line-1': '踯/蹢/踟三种字形；正义录文作蹢躅，现文的底本仍不明确，保留待核。',
    'zy-49-tuan': '信之/信也、时/时义、巳/已存在录文分歧；不得把三个不同见证各取一段拼成所谓原文，待选定底本后处理。',
    'zy-62-tuan': '正义正文支持现文开头小过、小者过而亨也，末句飞鸟遗之音（无新增有）；不按Kanripo/维基各自多出的字补库。',
}
corrections = []
adjudicated = []
for row in rows:
    eid = row['evidence_id']
    assert all(c['aligned'] and c['reference_text'] for c in row['comparisons'])
    status = {'matches_two_transcriptions': 'matched_two_normalized',
              'matches_one_transcription': 'matched_one_with_other_witness_difference',
              'differs_from_both': 'needs_edition_review'}[row['status']]
    note = notes.get(eid, '')
    if eid in fixes:
        before, after, excerpt, reason = fixes[eid]
        source = additional[row['hexagram_id']]
        assert row['current_text'].count(before) == 1
        assert excerpt in (OUT / source['text_file']).read_text()
        corrected = row['current_text'].replace(before, after)
        status = 'supported_transcription_correction'
        note = reason + '；两路逐条录文和第三渠道经典文本一致支持修正，未宣称逐字影印审定。'
        corrections.append(dict(evidenceId=eid, before=before, after=after,
            originalText=row['current_text'], correctedText=corrected,
            reason=reason, supportingUrls=[c['url'] for c in row['comparisons']] + [source['url']],
            additionalClassicalExcerpt=excerpt, applied=False,
            review_status='machine_assisted', humanReviewed=False))
    elif eid in ['zy-5-line_xiang-2', 'zy-62-tuan']:
        status = 'current_supported_by_additional_witness'
    elif eid == 'zy-22-tuan':
        status = 'attested_edition_variant'
    elif row['status'] == 'differs_from_both' and eid not in ['zy-14-line_xiang-4', 'zy-44-line-1', 'zy-49-tuan']:
        status = 'orthographic_or_word_variant_retained'
        if not note:
            note = '仅见遁/遯、熏/薰、啕/咷类用字差异，保留原录文并记录；不借规范化强行覆盖。'
    elif not note and row['status'] == 'matches_one_transcription':
        note = '一份对照录文支持现文，另一份有差异；不等于现文必错，也不等于已完成版本校勘。详情见comparisons。'
    record = dict(row)
    record.update(adjudication_status=status, note=note, change_applied=False,
                  review_status='machine_assisted')
    if row['hexagram_id'] in additional:
        source = additional[row['hexagram_id']]
        record['additional_source'] = source
    adjudicated.append(record)

assert len(adjudicated) == len({r['evidence_id'] for r in adjudicated}) == 964
(OUT / 'adjudicated.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in adjudicated))
(OUT / 'corrections.json').write_text(json.dumps(corrections, ensure_ascii=False, indent=2) + '\n')
summary = dict(total=len(adjudicated), status_counts=dict(Counter(r['adjudication_status'] for r in adjudicated)),
               correction_count=len(corrections), human_reviewed=False, runtime_corpus_changed=False)
(OUT / 'adjudication-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(summary, ensure_ascii=False))
