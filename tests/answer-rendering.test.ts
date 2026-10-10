import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { AnswerBody } from '../src/components/AnswerBody';
import type { Answer } from '../shared/types';

function render(answer: Answer) {
  return renderToStaticMarkup(createElement(AnswerBody, {
    answer, referenceNumbers: new Map([['e1', 1], ['e2', 2]]), referenceId: n => `reference-${n}`,
  }));
}

test('mixed prose places a citation after its supported claim, before practical advice', () => {
  const html = render({ status: 'answered', paragraphs: [{ text: '坎中有重险之意。店里人多的话，先预约也好。',
    citations: [{ evidenceId: 'e1', quote: '重险也', claim: '坎中有重险之意。' }] }],
    limitations: ['这条内部备注不另起一份报告。'] });
  assert.match(html, /坎中有重险之意。<sup/);
  assert.match(html, /<\/sup>店里人多的话，先预约也好。/);
  assert.ok(!html.includes('内部备注'));
  assert.ok(!html.includes('answer-summary'));
  assert.ok(!html.includes('limitations'));
});

test('legacy summary, paragraphs and uncertainties remain visible as ordinary body text', () => {
  const html = render({ status: 'insufficient', summary: '旧记录的概括。', paragraphs: [{ kind: 'interpretation',
    text: '旧正文。', citations: [{ evidenceId: 'e1', quote: '原文' }] }], limitations: ['尚未核实某项条件。'] });
  assert.match(html, /旧记录的概括。/);
  assert.match(html, /旧正文。<sup/);
  assert.match(html, /尚未核实某项条件。/);
  assert.equal((html.match(/class="answer-paragraph"/g) || []).length, 3);
  assert.ok(!html.includes('answer-summary'));
});

test('multiple citations retain distinct reference links at the same claim boundary', () => {
  const html = render({ status: 'answered', paragraphs: [{ text: '这句有所依据。其后是现实建议。', citations: [
    { evidenceId: 'e1', quote: '原文一', claim: '这句有所依据。' },
    { evidenceId: 'e2', quote: '原文二', claim: '这句有所依据。' },
    { evidenceId: 'e1', quote: '原文一', claim: '这句有所依据。' },
  ] }] });
  assert.equal((html.match(/href="#reference-1"/g) || []).length, 1);
  assert.equal((html.match(/href="#reference-2"/g) || []).length, 1);
  assert.match(html, /<\/sup>其后是现实建议。/);
});

test('body and claim render as escaped text; unknown references do not create broken links', () => {
  const html = render({ status: 'answered', paragraphs: [{ text: '<script>alert(1)</script>正文', citations: [
    { evidenceId: 'missing', quote: '原文', claim: '<script>alert(1)</script>' },
  ] }] });
  assert.ok(!html.includes('<script>'));
  assert.match(html, /&lt;script&gt;/);
  assert.ok(!html.includes('<sup'));
});
