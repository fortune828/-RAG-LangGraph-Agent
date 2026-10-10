import type { calculateHexagram } from '../src/lib/yijing';

export type Cast = ReturnType<typeof calculateHexagram> & {
  id: string; method: 'three-coins'; algorithmVersion: string; createdAt: string;
  coins?: (0 | 1)[][];
};
export type Section = 'judgment' | 'line' | 'tuan' | 'xiang' | 'line_xiang' | 'special';
export type EvidenceLayer = 'classic' | 'commentary' | 'subcommentary' | 'modern_editorial';
export interface Evidence {
  id: string; hexagramId: number; name: string; section: Section; line: number | null;
  text: string; source: string; locator: string; version: string; review: string;
  workId?: string; workTitle?: string; sourceId?: string; editionId?: string; editionLabel?: string;
  layer?: EvidenceLayer; authorIds?: string[]; authorNames?: string[];
  anchorIds?: string[]; commentsOn?: string[]; reviewStatus?: string; releaseId?: string;
  textOriginal?: string;
  correctionHistory?: { auditId: string; parentReleaseId: string; date: string;
    before: string; after: string; reason: string; sourceUrls: string[];
    reviewMethod: 'machine_assisted'; humanReviewed: false }[];
  rawLocator?: { file: string; startLine: number; endLine: number; pageLabels: string[] };
}
export interface Answer {
  status: 'answered' | 'insufficient';
  // summary and kind are read-only compatibility fields for stored legacy answers.
  summary?: string;
  paragraphs: { kind?: 'interpretation' | 'reflection'; text: string;
    citations: { evidenceId: string; quote: string; claim?: string }[] }[];
  limitations?: string[];
}
export interface TraceEvent {
  step: string; status: string; durationMs?: number; tool?: string;
  args?: unknown; evidenceIds?: string[]; code?: string;
  promptTokens?: number; completionTokens?: number; totalTokens?: number;
  releaseId?: string; channel?: string; coverageStatus?: string; missingTargets?: string[];
  truncated?: boolean; droppedEvidenceIds?: string[]; evidenceCharacters?: number;
  hits?: { passageId: string; channel: string; score: number | null; matchReason: string }[];
}
export interface Turn {
  id: string; question: string; mode: 'initial' | 'followup'; status: 'running' | 'completed' | 'failed';
  answer?: Answer; evidence: Evidence[]; trace: TraceEvent[]; error?: string;
  createdAt: string; durationMs?: number; model: string;
  releaseId?: string;
}
export interface Session {
  id: string; question: string; cast: Cast; turns: Turn[]; createdAt: string;
  releaseId?: string;
}
