import { Fragment } from 'react';
import type { Answer } from '../../shared/types';

type Paragraph = Answer['paragraphs'][number];

// Attach each source to the claim it supports, rather than to all advice in a paragraph.
// Older saved citations have no claim and retain their original paragraph-end placement.
export function citationGroups(paragraph: Paragraph) {
  const groups = new Map<number, Map<string, string | undefined>>();
  for (const citation of paragraph.citations) {
    const start = citation.claim ? paragraph.text.indexOf(citation.claim) : -1;
    const end = start >= 0 ? start + citation.claim!.length : paragraph.text.length;
    const citations = groups.get(end) || new Map<string, string | undefined>();
    citations.set(citation.evidenceId, start >= 0 ? citation.claim : undefined);
    groups.set(end, citations);
  }
  return [...groups].sort(([a], [b]) => a - b);
}

export function AnswerBody({ answer, referenceNumbers, referenceId }: {
  answer: Answer; referenceNumbers: Map<string, number>; referenceId: (number: number) => string;
}) {
  const legacy = answer.summary !== undefined || answer.paragraphs.some(p => p.kind !== undefined);
  function marker(id: string, claim?: string) {
    const number = referenceNumbers.get(id);
    return number ? <sup key={id} className="reference-marker"><a href={`#${referenceId(number)}`}
      aria-label={`查看参考文献 ${number}`} title={claim ? `对应内容：${claim}` : undefined}
      onClick={event => {
        event.preventDefault();
        const reference = document.getElementById(referenceId(number)) as HTMLDetailsElement | null;
        if (!reference) return;
        const panel = reference.closest('.answer-references') as HTMLDetailsElement | null;
        if (panel) panel.open = true;
        reference.open = true;
        reference.querySelector('summary')?.focus({ preventScroll: true });
        reference.scrollIntoView({ block: 'nearest' });
      }}>[{number}]</a></sup> : null;
  }
  return <div className="answer-body">
    {legacy && answer.summary && <section className="answer-paragraph"><p>{answer.summary}</p></section>}
    {answer.paragraphs.map((paragraph, index) => {
      let start = 0;
      const pieces = citationGroups(paragraph).map(([end, citations]) => {
        const text = paragraph.text.slice(start, end);
        start = end;
        return <Fragment key={end}>{text}{[...citations].map(([id, claim]) => marker(id, claim))}</Fragment>;
      });
      return <section key={index} className="answer-paragraph"><p>{pieces}{paragraph.text.slice(start)}</p></section>;
    })}
    {legacy && answer.limitations?.map((text, index) => <section key={`legacy-limit-${index}`} className="answer-paragraph"><p>{text}</p></section>)}
  </div>;
}
