import type { Evidence } from './types';
export type KnowledgeLayer = 'classic' | 'commentary' | 'subcommentary' | 'modern_editorial';
export interface RetrievalHit { passageId: string; channel: 'exact' | 'lexical' | 'semantic'; score: number | null; matchReason: string }
export interface RetrievalResult {
  evidence: Evidence[]; hits: RetrievalHit[]; releaseId: string;
  coverageStatus: 'covered' | 'partial' | 'not_ingested' | 'unknown';
  missingTargets: string[]; noHitReason?: 'no_match' | 'filtered_out' | 'ambiguous'; truncated: boolean;
}
export interface Retriever {
  fetchExact(args: unknown, releaseId?: string): RetrievalResult;
  searchLexical(args: unknown, releaseId?: string): RetrievalResult;
  searchSemantic?: (args: unknown, releaseId?: string) => Promise<RetrievalResult>;
}
