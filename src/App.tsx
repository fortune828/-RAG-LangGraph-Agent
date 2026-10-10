import { useEffect, useRef, useState } from 'react';
import { BookOpen, ArrowUpRight, RotateCcw, Sparkles, Plus, LoaderCircle, Trash2, Mountain } from 'lucide-react';
import { Hexagram } from './components/Hexagram';
import type { Session, Turn, Evidence } from '../shared/types';

type Access = { id: string; token: string; question: string };
const STORAGE_KEY = 'yijing-session-v2';
const errors: Record<string, string> = {
  API_KEY_MISSING: '服务端尚未配置模型密钥。', MODEL_HTTP_401: '模型鉴权失败，请检查服务端密钥。',
  MODEL_HTTP_402: '模型账户余额不足。', MODEL_HTTP_429: '模型服务繁忙，请稍后重试。',
  MODEL_TIMEOUT_OR_NETWORK: '模型请求超时或网络中断，当前卦象已保留。',
  MODEL_INCOMPLETE_RESPONSE: '模型返回不完整，请对同一结果重试。',
  ANSWER_VALIDATION_FAILED: '回答未通过核验，已停止展示。可以保留本卦重试解释。',
  AGENT_EXECUTION_FAILED: '本次解读中断，已有卦象和历史回答已保留。请重试解释。',
  SESSION_BUSY: '当前会话还在生成，请稍候。', SERVER_BUSY: '服务正在处理其他请求，请稍后重试。',
  RATE_LIMITED: '请求过于频繁，请稍后重试。', SESSION_ACCESS_DENIED: '会话不存在或访问凭据失效。',
  SERVER_RESTARTED: '服务重启中断了解读，请对同一结果重试。', SESSION_LIMIT: '当前会话已达到轮次上限，请新建起卦。',
  RELEASE_UNAVAILABLE: '当前会话使用的知识版本暂时不可用，历史回答和卦象已保留。请恢复相应知识版本后重试。',
  CORPUS_HASH_MISMATCH: '资料文件校验失败，已停止解读。历史回答和卦象已保留，请恢复未损坏的资料后重试。',
  CHECKPOINT_VERSION_MISMATCH: '服务已更新，这轮中断的解读无法直接续跑。原卦和已有回答仍保留，请在下方沿用本卦重新提问。',
  REQUIRED_EVIDENCE_BUDGET_EXCEEDED: '本次必要原文超出可用上下文，尚未请求模型。请缩小问题范围后追问。',
};
const describeError = (code: string) => errors[code] || '这次请求未完成。请稍后重试，已有卦象不会改变。';
const layerLabels = { classic: '原典', commentary: '历代注解', subcommentary: '疏解', modern_editorial: '项目现代说明' };
function SourceMeta({ evidence: e }: { evidence: Evidence }) {
  return <p className="source-meta">{layerLabels[e.layer || 'classic']}
    {e.authorNames?.length ? ` · ${e.authorNames.join('、')}` : ''}
    {e.workTitle ? ` · 《${e.workTitle}》` : ''}
    {` · ${e.editionLabel || e.version}`}<br/>
    {e.reviewStatus === 'human_reviewed' ? '人工复核' : '未完成人工校勘'} · {e.review}
  </p>;
}

function TurnCard({ turn, onRetry, busy }: { turn: Turn; onRetry: () => void; busy: boolean }) {
  const answer = turn.answer;
  const byId = new Map(turn.evidence.map(e => [e.id, e]));
  const references = new Map<string, { evidence: Evidence; quotes: string[] }>();
  for (const paragraph of answer?.paragraphs || []) for (const citation of paragraph.citations) {
    const evidence = byId.get(citation.evidenceId);
    if (!evidence) continue;
    const reference = references.get(evidence.id) || { evidence, quotes: [] };
    if (!reference.quotes.includes(citation.quote)) reference.quotes.push(citation.quote);
    references.set(evidence.id, reference);
  }
  for (const evidence of turn.evidence) {
    if (!references.has(evidence.id)) references.set(evidence.id, { evidence, quotes: [] });
  }
  const referenceNumbers = new Map([...references.keys()].map((id, i) => [id, i + 1]));
  const referenceId = (number: number) => `turn-${turn.id}-reference-${number}`;
  const tokens = turn.trace.reduce((n, e) => n + (e.totalTokens || 0), 0);
  return <article className="turn-card">
    <div className="eyebrow">{turn.mode === 'initial' ? '初次解读' : '沿着这个问题，继续探寻'}</div>
    <h3 className="question-title">{turn.question}</h3>
    {turn.status === 'running' && <p className="loading"><LoaderCircle className="spin" size={18}/> 正在查阅原文并整理回答…</p>}
    {turn.status === 'failed' && <div role="alert" className="error-box"><p>{describeError(turn.error || '')}</p>
      {turn.error === 'CHECKPOINT_VERSION_MISMATCH' || turn.error === 'REQUIRED_EVIDENCE_BUDGET_EXCEEDED'
        ? <a href="#followup" className="text-button" onClick={event => { event.preventDefault(); document.getElementById('followup')?.focus(); }}>沿用本卦重新提问</a>
        : <button onClick={onRetry} disabled={busy} className="text-button"><RotateCcw size={15}/>保留卦象，重试解释</button>}</div>}
    {answer && <>
      <div className="answer-summary">{answer.summary}</div>
      {answer.status === 'insufficient' && <span className="badge">可用材料不足</span>}
      {answer.paragraphs.map((p, i) => <section key={i} className="answer-paragraph"><p>{p.text}
        {[...new Set(p.citations.map(c => c.evidenceId))].map(id => {
          const number = referenceNumbers.get(id);
          return number ? <sup key={id} className="reference-marker"><a href={`#${referenceId(number)}`}
            aria-label={`查看参考文献 ${number}`} onClick={event => {
              event.preventDefault();
              const reference = document.getElementById(referenceId(number)) as HTMLDetailsElement | null;
              if (reference) {
                const referencesPanel = reference.closest('.answer-references') as HTMLDetailsElement | null;
                if (referencesPanel) referencesPanel.open = true;
                reference.open = true;
                reference.querySelector('summary')?.focus({ preventScroll: true });
                reference.scrollIntoView({ block: 'nearest' });
              }
            }}>[{number}]</a></sup> : null;
        })}</p></section>)}
      {!!answer.limitations.length && <aside className="limitations">{answer.limitations.map((s, i) => <p key={i}>{s}</p>)}</aside>}
    </>}
    {!!turn.trace.length && <details className="trace"><summary>查看执行记录{turn.durationMs ? ` · ${(turn.durationMs / 1000).toFixed(1)} 秒` : ''}</summary>
      <p>模型 {turn.model} · {tokens} tokens · 工具调用 {turn.trace.filter(t => t.step === 'tool').length} 次</p>
      {turn.releaseId && <p>知识版本 {turn.releaseId}</p>}
      <ol>{turn.trace.map((e, i) => <li key={i}>{e.step === 'retrieve' ? '读取原典与相关注解' : e.step === 'model' ? '模型生成' : e.step === 'tool' ? `补充检索 ${e.tool}` : e.step === 'replan' ? '复核补充检索需求' : e.step === 'execution' ? '执行中断' : '结构、引文与归属校验'}：{e.status}{e.code ? ` (${e.code})` : ''}
        {e.evidenceIds?.length ? ` · ${e.evidenceIds.length} 条依据` : ''}
        {e.truncated ? ' · 资料已按预算截取' : ''}
        {e.coverageStatus === 'not_ingested' ? ' · 本库未收录该范围' : e.coverageStatus === 'partial' ? ' · 仅覆盖部分范围' : ''}</li>)}</ol>
      <p>校验检查来源、文字匹配与显式作者归属，不等于已经验证解释的语义正确。</p>
    </details>}
    {!!references.size && <details className="answer-references">
      <summary>查看参考资料 <span>{references.size} 条</span></summary>
      {[...references.values()].map(({ evidence: e, quotes }, i) => <details key={e.id} id={referenceId(i + 1)} className="citation">
        <summary><span className="reference-number">[{i + 1}]</span><BookOpen size={13}/>
          <span className="reference-title">{e.locator}</span><span className="reference-hint">{quotes.length ? '查看原文' : '补充资料 · 未直接引用'}</span></summary>
        <SourceMeta evidence={e}/>
        {quotes.map(quote => <blockquote key={quote}>“{quote}”</blockquote>)}
        <p className="source-text">{e.text}</p>
        <a href={e.source} target="_blank" rel="noreferrer">{e.correctionHistory?.length ? '打开采集原文（修订前）' : '打开原文来源'} <ArrowUpRight size={13}/></a>
        {!!e.correctionHistory?.length && <details><summary>查看文字修订依据</summary>
          {e.correctionHistory.map((c, i) => <div key={i}>
            <p>「{c.before}」→「{c.after}」：{c.reason}（机器辅助核对，未完成人工校勘）</p>
            <ul>{c.sourceUrls.map((url, j) => <li key={url}><a href={url} target="_blank" rel="noreferrer">核对来源 {j + 1}</a></li>)}</ul>
          </div>)}
        </details>}
      </details>)}
    </details>}
  </article>;
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [question, setQuestion] = useState('');
  const [followup, setFollowup] = useState('');
  const [busy, setBusy] = useState(false);
  const [restoring, setRestoring] = useState(true);
  const [error, setError] = useState('');
  const access = useRef<Access | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  async function api(path: string, body?: unknown, method = 'POST') {
    const response = await fetch('/api' + path, { method: body === undefined && method === 'POST' ? 'GET' : method,
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + (access.current?.token || '') },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
    if (response.status === 204) return null;
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'NETWORK');
    return data;
  }
  useEffect(() => {
    let cancelled = false;
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) {
        access.current = JSON.parse(saved);
        api('/sessions/' + access.current!.id).then(s => { if (!cancelled) setSession(s); })
          .catch(() => { if (!cancelled) { setQuestion(access.current?.question || ''); setError('未能恢复会话。可再次提交原问题，系统会复用原请求标识。'); } })
          .finally(() => { if (!cancelled) setRestoring(false); });
      } else setRestoring(false);
    } catch { localStorage.removeItem(STORAGE_KEY); access.current = null; setRestoring(false); }
    return () => { cancelled = true; };
  }, []);
  const running = session?.turns.some(t => t.status === 'running') || false;
  useEffect(() => {
    if (!running || busy || !session) return;
    const timer = setInterval(() => { api('/sessions/' + session.id).then(setSession).catch(() => setError('暂时无法获取进度，请刷新重试。')); }, 2000);
    return () => clearInterval(timer);
  }, [running, busy, session?.id]);
  async function submitTurn(s: Session, text: string, retry?: Turn) {
    const requestId = retry?.id || crypto.randomUUID();
    setBusy(true); setError('');
    try {
      // Read-after-write polling survives a browser reload without issuing another model call.
      setSession({ ...s, turns: retry ? s.turns.map(t => t.id === retry.id ? { ...t, status: 'running' } : t)
        : [...s.turns, { id: requestId, question: text, mode: s.turns.length ? 'followup' : 'initial',
          status: 'running', createdAt: new Date().toISOString(), evidence: [], trace: [], model: '' }] });
      const result = await api(`/sessions/${s.id}/turns`, { requestId, question: text, retry: Boolean(retry) });
      setSession(result); setFollowup('');
    } catch (e) {
      setError(describeError(e instanceof Error ? e.message : 'NETWORK'));
      try { setSession(await api('/sessions/' + s.id)); } catch { setSession(s); }
    } finally { setBusy(false); }
  }
  async function start() {
    if (!question.trim() || busy) return;
    setBusy(true); setError('');
    try {
      if (!access.current || access.current.question !== question.trim()) {
        access.current = { id: crypto.randomUUID(), token: crypto.randomUUID(), question: question.trim() };
        localStorage.setItem(STORAGE_KEY, JSON.stringify(access.current));
      }
      const s: Session = await api('/sessions', { requestId: access.current.id, question: question.trim() });
      setSession(s);
      if (!s.turns.length) await submitTurn(s, s.question);
    } catch (e) { setError(describeError(e instanceof Error ? e.message : 'NETWORK')); }
    finally { setBusy(false); }
  }
  function reset() { access.current = null; localStorage.removeItem(STORAGE_KEY); setSession(null); setQuestion(''); setFollowup(''); setError(''); }
  async function remove() {
    if (!session) return; setBusy(true);
    try { await api('/sessions/' + session.id, undefined, 'DELETE'); reset(); }
    catch (e) { setError(describeError(e instanceof Error ? e.message : 'NETWORK')); }
    finally { setBusy(false); }
  }
  useEffect(() => { if (session?.turns.length) bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [session?.turns.length]);
  const locked = busy || running || restoring;
  return <div className="app-shell">
    <header className="site-header"><a className="brand" href="/"><Mountain size={22}/><span>见素问微</span></a><span className="header-note">以原文为据 · 以变化观照</span></header>
    <main>
      <div className="intro"><div className="eyebrow">THE BOOK OF CHANGES / 易经文化解读</div><h1>把困惑，放在变化中看。</h1><p>围绕一个核心问题诚心发问，从一卦出发查阅原文、理解当下。新的问题请新建聊天并重新起卦。</p></div>
      {error && <div role="alert" className="error-box">{error}</div>}
      {restoring ? <p className="loading">正在恢复会话…</p> : !session ? <section className="input-card">
        <label htmlFor="question">此刻，你想探讨什么？</label><textarea id="question" maxLength={2000} value={question} onChange={e => setQuestion(e.target.value)} placeholder="例如：面对新的工作机会，我该如何看待进退与取舍？" disabled={busy}/>
        <div className="input-footer"><span>随机三枚硬币起卦 · 六爻自下而上</span><button className="primary" disabled={locked || !question.trim()} onClick={start}>{busy ? <LoaderCircle className="spin" size={17}/> : <Sparkles size={17}/>}起卦并解读</button></div>
        <p className="small-note">用于文化理解与自我反思，不把卦象作为现实预测。问题会发送给 DeepSeek 生成解读。</p>
      </section> : <div className="workspace">
        <aside className="cast-panel"><div className="eyebrow">本次卦象 · 追问时保持不变</div><div className={`cast-grid${session.cast.changingIndices.length ? '' : ' cast-grid-single'}`}>
          <div><span>本卦</span><Hexagram binary={session.cast.originalBinary} changingIndices={session.cast.changingIndices}/><h2>{session.cast.original.name}</h2></div>
          {!!session.cast.changingIndices.length && <div><span>变卦</span><Hexagram binary={session.cast.changedBinary} isChanged/><h2>{session.cast.changed.name}</h2></div>}
        </div><p className="moving">{session.cast.changingIndices.length ? `动爻：${session.cast.changingIndices.map(i => `第${i + 1}爻`).join('、')}` : '本次没有动爻，本卦与变卦相同。'}</p>
          <p className="cast-values">六爻值：{session.cast.lines.join(' · ')}<br/>每次解释重试均复用这组结果。</p>
          <details className="cast-method"><summary>查看起卦过程与取值</summary>
            <p>三枚硬币投掷六次，按初爻到上爻（自下而上）记录。每枚保存为 0 或 1，对应 2 或 3，相加得到该爻值。</p>
            <p>6 为老阴、7 为少阳、8 为少阴、9 为老阳；6 和 9 为动爻，阴阳互换得到变卦。</p>
            {session.cast.coins?.length === 6 ? <ol>{session.cast.coins.map((row, i) => <li key={i}>
              {['初爻', '二爻', '三爻', '四爻', '五爻', '上爻'][i]}：记录 {row.join('、')} → {row.map(bit => bit + 2).join(' + ')} = {session.cast.lines[i]}
            </li>)}</ol> : <p>这份旧记录未保存单币结果，仅能查看已保存的六爻值。</p>}
          </details>
          <div className="panel-actions"><button disabled={locked} onClick={reset} className="text-button"><Plus size={15}/>新建起卦</button><button disabled={locked} onClick={remove} className="text-button"><Trash2 size={14}/>删除本次会话</button></div>
          <p className="small-note">当前访问凭据保存在此浏览器。会话存于本地服务，可主动删除；执行日志不保存问题正文。</p>
        </aside>
        <div className="conversation">
          {!session.turns.length && <button className="primary" disabled={locked} onClick={() => submitTurn(session, session.question)}>开始解读已保存的卦象</button>}
          {session.turns.map(t => <TurnCard key={t.id} turn={t} busy={locked} onRetry={() => submitTurn(session, t.question, t)}/>)}
          {!!session.turns.length && <form className="followup-card" onSubmit={e => { e.preventDefault(); if (followup.trim() && !locked) void submitTurn(session, followup.trim()); }}>
            <label htmlFor="followup">沿用当前卦象，继续追问</label><textarea id="followup" maxLength={2000} value={followup} onChange={e => setFollowup(e.target.value)} disabled={locked} placeholder="例如：我只有三个月积蓄，最担心收入中断，可以先做哪些准备？"/>
            <div className="input-footer"><span>仅补充或追问本题；新问题请点“新建起卦”</span><button className="primary" disabled={locked || !followup.trim()}>继续探讨 <ArrowUpRight size={16}/></button></div>
          </form>}<div ref={bottom}/>
        </div>
      </div>}
    </main>
    <footer>原文采用固定版本的学习语料，尚未完成逐条人工校勘。引文可追溯，解释仍需结合实际审慎理解。<br/>三枚硬币起卦 · 原文与注解可查 · 同卦追问</footer>
  </div>;
}
