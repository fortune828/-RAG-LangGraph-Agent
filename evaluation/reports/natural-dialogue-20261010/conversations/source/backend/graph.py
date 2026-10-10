"""Executable Agent graph. Read build_graph() first, then follow each node.

Each turn has its own durable thread. Business history remains in the session
store; checkpoints record *execution progress*, not permission or idempotency.
"""
import json
import copy
import re
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .knowledge import (TOOLS, coverage, execute_tool, initial_evidence, load_release,
                        normalize_search, canonical_tool_args)
from .llm import deepseek_chat
from .prompts import PROMPT_VERSION, ROUTER, SYSTEM, RISK_SYSTEM
from .validation import AUTHORS, WORKS, fallback, matches_target, named_hexagrams, requested_targets, validate_answer

GRAPH_VERSION = 'python-graph-v10'


class AgentState(TypedDict, total=False):
    session: dict
    question: str
    mode: str
    release: str
    intent: str
    scope_ids: list[int] | None
    evidence: list[dict]
    targets: list[dict]
    messages: list[dict]
    response: dict
    answer: dict | None
    trace: list[dict]
    attempts: int
    replans: int
    repairs: int
    seen: list[str]
    errors: list[str]
    next_step: str
    require_citations: bool
    prompt_version: str
    graph_version: str
    started: float


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def pack_evidence(required, optional, max_chars=16000):
    selected = {e['id']: e for e in required}
    chars = sum(len(dumps(e)) for e in selected.values())
    if chars > max_chars or len(selected) > 36:
        raise ValueError('REQUIRED_EVIDENCE_BUDGET_EXCEEDED')
    dropped = []
    for e in optional:
        if e['id'] in selected:
            continue
        size = len(dumps(e))
        if chars + size > max_chars or len(selected) >= 36:
            dropped.append(e['id'])
        else:
            selected[e['id']] = e
            chars += size
    return list(selected.values()), dropped


def classify(question, mode):
    risk_question = re.sub(r'投资(?:时间|精力|心血)', '', question)
    if re.search(r'自杀|自殺|轻生|不想活|自残|服药|用药|停药|诊断|诊療|医疗|治病|急救|投资|股票|诉讼|法律', risk_question):
        return 'out_of_scope_or_risk'
    if re.search(r'重新起卦|再起一卦|再.*随机.*卦|新建起卦', question) or (
            mode != 'initial' and re.search(r'换个话题|换一个话题|先不谈.+(?:改问|问)|不说.+(?:改问|问)', question)):
        return 'new_reading_request'
    if re.search(r'出处|出自|原文|逐字|哪一卦|哪一爻|(?:王弼|程颐|朱熹|焦循).*(?:说过|解|认为|观点|怎么说)|(?:注解|解释).*(?:王弼|程颐|朱熹|焦循)', question):
        return 'source_request'
    if mode == 'initial' or re.search(r'卦|爻|彖|象传|象傳|古文|注解', question):
        return 'explanation_followup'
    return 'situation_followup'


def evidence_scope(question, cast, intent):
    # Unknown-source discovery is the one explicit global-lookup policy.
    # Normal conversational prompts never gain global scope from model arguments.
    if intent == 'source_request' and re.search(r'出处|出自|哪一卦|哪一爻|是不是说过|是否说过|记得.*(?:大意|意思)|大意是', question) and not re.search(r'本卦|当前卦|本次|刚才|第?[一二三四五六1-6]爻|初九|初六|上九|上六|[九六][二三四五]', question) and not named_hexagrams(question):
        return None
    return sorted(set([cast['original']['number'], cast['changed']['number']] + named_hexagrams(question)))


def target_availability(state):
    """Corpus absence is established by exact metadata, never by a search miss.

    Unknown book names remain unresolved (they may be aliases). An explicitly
    recognized author absent from this release is still a definite coverage gap.
    """
    corpus = load_release(state['release'])[0]
    books = re.findall(r'《([^》]{1,80})》', state['question']) if state.get('intent', 'source_request') == 'source_request' else []
    known = {normalize_search(e.get('workTitle', '周易')) for e in corpus}
    known.update(normalize_search(n) for names in WORKS.values() for n in names)
    result = dict(available=[], retrievable=[], unavailable=[], unresolvedBooks=[b for b in books if normalize_search(b) not in known])
    for target in state['targets']:
        found = any(matches_target(e, target) for e in corpus)
        if not found:
            result['unavailable'].append(target)
        elif not result['unresolvedBooks']:
            key = 'available' if any(matches_target(e, target) for e in state['evidence']) else 'retrievable'
            result[key].append(target)
    return result


def missing_targets(state):
    return target_availability(state)['retrievable']


def generation_evidence(state):
    """Final answers see relevant evidence, not the router's working transcript."""
    if state['intent'] != 'source_request':
        return state['evidence']
    if target_availability(state)['unresolvedBooks']:
        return []
    if state['targets']:
        return [e for e in state['evidence'] if any(matches_target(e, t) for t in state['targets'])]
    if state['scope_ids'] is None:
        retrieved = {eid for event in state['trace'] if event['step'] == 'tool'
                     for eid in event.get('evidenceIds', [])}
        return [e for e in state['evidence'] if e['id'] in retrieved]
    return state['evidence']


def target_label(target):
    parts = [f"第{target['hexagramId']}卦"] if target.get('hexagramId') else []
    if target.get('line'):
        parts.append(f"第{target['line']}爻")
    if target.get('authorId'):
        parts.append(AUTHORS[target['authorId']][0])
    if target.get('workId'):
        parts.append('《' + WORKS[target['workId']][0] + '》')
    if target.get('section'):
        parts.append({'line': '爻辞', 'line_xiang': '小象', 'judgment': '卦辞',
                      'xiang': '大象', 'tuan': '彖传', 'special': '用九或用六'}[target['section']])
    if target.get('layer') == 'commentary':
        parts.append('注解材料（不是经典爻辞）')
    return ' / '.join(parts)


def history(session):
    """Keep recent user corrections before spending the budget on assistant prose.

    Excerpts preserve both ends, including legacy limitations; they are not
    summaries or user facts. Information outside the six-turn window is unknown.
    """
    def excerpt(text, limit):
        if len(text) <= limit:
            return text
        marker = '\n（中间内容省略）\n'
        room = max(0, limit - len(marker))
        head = room * 3 // 5
        tail = room - head
        return text[:head] + marker + (text[-tail:] if tail else '')

    turns = [t for t in session['turns'] if t['status'] == 'completed'][-6:]
    texts, selected = [], []
    for turn in turns:
        answer = turn.get('answer') or {}
        text = '\n'.join([answer.get('summary', '')] +
                         [p['text'] for p in answer.get('paragraphs', [])] + answer.get('limitations', []))
        texts.append(text)
        selected.append(dict(user=turn['question'][:2000], assistant=excerpt(text, 4000)))
    # Only assistant excerpts shrink. Recent user corrections never disappear
    # because an older assistant happened to write a long answer.
    while selected and len(dumps(selected)) > 16000:
        index = max(range(len(selected)), key=lambda i: len(selected[i]['assistant']))
        length = len(selected[index]['assistant'])
        if length <= 100:
            break  # API user text limits normally make this unreachable.
        selected[index]['assistant'] = excerpt(texts[index], max(100, length // 2))
    return selected


def new_question_answer():
    return dict(status='answered',
        paragraphs=[dict(text='请点击“新建起卦”，围绕新的核心问题重新发问。本次卦象不改变，新聊天不会沿用这里的对话。', citations=[])],
        limitations=[])


def initial_state(session, question, mode):
    return dict(session=session, question=question, mode=mode, release=session['releaseId'],
                trace=[], attempts=0, replans=0, repairs=0, seen=[], errors=[], answer=None,
                prompt_version=PROMPT_VERSION, graph_version=GRAPH_VERSION, started=time.time())


def build_graph(checkpointer, chat=deepseek_chat, interrupt_before=None, *, route_policy=None, observe=None):
    """Dependencies are injected once; graph state never contains clients or secrets."""
    # Optional evaluation seams. The API passes neither; policies never receive gold.
    def emit(event):
        if observe is not None:
            observe(event)

    def prepare(state):
        started = time.monotonic()
        session, question, release = state['session'], state['question'], state['release']
        cast = session['cast']
        intent = classify(question, state['mode'])
        scope = evidence_scope(question, cast, intent)
        base = initial_evidence(cast, question, release)
        previous = [e for t in [t for t in session['turns'] if t['status'] == 'completed'][-2:]
                    for e in t['evidence'] if e.get('releaseId', e['version']) == release][-12:]
        previous = [e for e in previous if scope is None or e['hexagramId'] in scope]
        targets = requested_targets(question, cast)
        if scope is None:
            targets = [dict(t, hexagramId=None) for t in targets]
        extras = [e for e in base if e['layer'] != 'classic'] + previous
        extras.sort(key=lambda e: not any(matches_target(e, t) for t in targets))
        evidence, dropped = pack_evidence([e for e in base if e['layer'] == 'classic'], extras)
        # Risk support and the instruction to start a new reading do not need
        # divination evidence. Do not tempt the model to keep interpreting it.
        practical_only = intent in ('out_of_scope_or_risk', 'new_reading_request')
        if practical_only:
            evidence, targets = [], []
        # Even a small first reading starts from the cast. Follow-ups can carry
        # its meaning forward without repeating citations; risk/handoff stays practical.
        requires = (state['mode'] == 'initial' and not practical_only) or bool(targets) or intent == 'source_request' or (
            intent == 'explanation_followup' and bool(re.search(r'卦|爻|彖|象传|象傳|古文|注解', question)))
        partial = dict(state, evidence=evidence, targets=targets)
        availability = target_availability(partial)
        gaps = availability['retrievable']
        known_missing = bool(targets) and len(availability['unavailable']) == len(targets)
        context = dict(question=session['question'], currentQuestion=question,
                       cast={key: cast[key] for key in ('id', 'lines', 'changingIndices')} | {
                           'lineTypes': [{6: '老阴', 7: '少阳', 8: '少阴', 9: '老阳'}[v] for v in cast['lines']],
                           'original': {k: cast['original'][k] for k in ('number', 'name')},
                           'changed': {k: cast['changed'][k] for k in ('number', 'name')}},
                       evidence=evidence, recentHistory=(
                           [dict(user=t['user']) for t in history(session)] if practical_only else history(session)),
                       knowledgeCoverage=coverage(release),
                       allowedHexagramIds=scope, intent=intent, requireCitations=requires,
                       requiredEvidenceTargetsForAnswered=targets, retrievableMissingTargets=gaps,
                       unavailableTargets=availability['unavailable'], unresolvedBooks=availability['unresolvedBooks'])
        messages = [dict(role='system', content=ROUTER), dict(role='user', content=dumps(context))]
        update = dict(intent=intent, scope_ids=scope, targets=targets, evidence=evidence, messages=messages,
                    require_citations=requires,
                    next_step='generate' if practical_only or known_missing or (
                        state['mode'] == 'initial' and not gaps and not (intent == 'source_request' and scope is None)) else 'route',
                    trace=state['trace'] + [dict(step='retrieve', status='ok', channel='fixed', releaseId=release,
                                                code=intent, evidenceIds=[e['id'] for e in evidence],
                                                truncated=bool(dropped), droppedEvidenceIds=dropped)])
        if intent == 'new_reading_request':
            update.update(next_step='finish', answer=new_question_answer())
            update['trace'].append(dict(step='handoff', status='ok', code='EXPLICIT_NEW_READING_REQUIRED'))
        emit(dict(kind='prepare', evidence=evidence, scopeIds=scope, targets=targets,
                  requireCitations=requires, nextStep=update['next_step'], intent=intent,
                  durationMs=round((time.monotonic() - started) * 1000)))
        return update

    async def invoke(messages, state, tools=None):
        before = time.monotonic()
        result = await chat(messages, tools=tools)
        usage = result.get('usage') or {}
        event = dict(step='model', status='ok', durationMs=round((time.monotonic() - before) * 1000),
                     promptTokens=usage.get('prompt_tokens'), completionTokens=usage.get('completion_tokens'),
                     totalTokens=usage.get('total_tokens'))
        return result['message'], state['trace'] + [event]

    async def route(state):
        if route_policy is None:
            response, trace = await invoke(state['messages'], state, TOOLS)
        else:
            response = route_policy(copy.deepcopy(state))
            trace = state['trace'] + [dict(step='route', status='ok', code='RULE_POLICY')]
            emit(dict(kind='rule_decision', response=response))
        try:
            decision = json.loads(response.get('content') or '{}')
        except (ValueError, TypeError):
            decision = {}
        if state['mode'] != 'initial' and isinstance(decision, dict) and decision.get('newTopic') is True:
            return dict(response=response, trace=trace + [dict(step='handoff', status='ok', code='NEW_CORE_QUESTION')],
                        answer=new_question_answer(), next_step='finish')
        calls = response.get('tool_calls') or []
        if len(calls) > 8:
            raise ValueError('TOOL_BATCH_TOO_LARGE')
        messages = state['messages'] + [response]
        if calls:
            return dict(response=response, messages=messages, trace=trace, next_step='tools')
        gaps = missing_targets(state)
        source_gap = state['intent'] == 'source_request' and state['scope_ids'] is None and state['attempts'] == 0
        if (gaps or source_gap) and state['replans'] < 1 and state['attempts'] < 3:
            messages.append(dict(role='user', content=dumps(dict(
                instruction='本轮明确查询新出处或缺失对象。上下文缺失不等于全库缺失，请重新判断并调用工具；此次反馈仅一次。',
                retrievableMissingTargets=gaps))))
            return dict(response=response, messages=messages, trace=trace + [dict(step='replan', status='requested', code='MISSING_RETRIEVABLE_TARGET')],
                        replans=state['replans'] + 1, next_step='route')
        return dict(response=response, messages=messages, trace=trace, next_step='generate')

    def tools_node(state):
        evidence, trace, messages = list(state['evidence']), list(state['trace']), list(state['messages'])
        seen, attempts = list(state['seen']), state['attempts']
        for call in state['response']['tool_calls']:
            name, args = call.get('function', {}).get('name', ''), None
            started = time.monotonic()
            before_attempts = attempts
            retrieved = None
            emit(dict(kind='tool_request', request=call, attemptsBefore=attempts))
            try:
                if attempts >= 3:
                    raise ValueError('TOOL_BUDGET_EXHAUSTED')
                attempts += 1
                raw = call['function']['arguments']
                if not isinstance(raw, str) or len(raw) > 3000:
                    raise ValueError('INVALID_TOOL_ARGUMENTS')
                args = canonical_tool_args(name, json.loads(raw))
                signature = name + json.dumps(args, sort_keys=True)
                if signature in seen:
                    raise ValueError('REPEATED_TOOL_CALL')
                seen.append(signature)
                result = execute_tool(name, args, state['release'], state['scope_ids'])
                if observe is not None:
                    retrieved = copy.deepcopy(result)
                evidence, dropped = pack_evidence(evidence, result['evidence'])
                accepted = {e['id'] for e in evidence}
                result['evidence'] = [e for e in result['evidence'] if e['id'] in accepted]
                result['truncated'] |= bool(dropped)
                result['droppedEvidenceIds'] = dropped
                event = dict(step='tool', tool=name, args=args, status='ok' if result['evidence'] else 'empty',
                             evidenceIds=[e['id'] for e in result['evidence']], releaseId=state['release'],
                             hits=result['hits'], coverageStatus=result['coverageStatus'], truncated=result['truncated'])
            except (ValueError, KeyError, TypeError) as exc:
                code = str(exc) if str(exc) in ('TOOL_BUDGET_EXHAUSTED', 'REPEATED_TOOL_CALL', 'UNKNOWN_TOOL', 'EVIDENCE_SCOPE_DENIED') else 'INVALID_TOOL_ARGUMENTS'
                result = dict(error=code)
                event = dict(step='tool', tool=name, status='error', code=code)
            event['durationMs'] = round((time.monotonic() - started) * 1000)
            emit(dict(kind='tool_result', requestId=call['id'], tool=name, args=args,
                      budgetCounted=attempts > before_attempts, attemptsAfter=attempts,
                      executionStatus='rejected' if event['status'] == 'error' else event['status'],
                      retrievedResult=retrieved, returnedResult=result, trace=event))
            trace.append(event)
            messages.append(dict(role='tool', tool_call_id=call['id'], content=dumps(result)))
        return dict(evidence=evidence, trace=trace, messages=messages, seen=seen, attempts=attempts,
                    next_step='generate' if attempts >= 3 else 'route')

    def generation_messages(state):
        # Dialog roles are preserved; old assistant text is neither user fact nor
        # freshly verified evidence. Tool transcripts stay in the checkpoint.
        availability = target_availability(state)
        notes = []
        notes += ['当前资料库未收录：' + target_label(t) for t in availability['unavailable']]
        if not availability['unavailable']:
            notes += ['书名尚未确认：' + b + '；不能仅据名称未匹配就断言全库未收录。' for b in availability['unresolvedBooks']]
        notes += ['本轮尚未取得所问材料：' + target_label(t) for t in availability['retrievable']]
        if state['require_citations']:
            notes.append('首次解读或所问传统内容须有简短依据；如有明确文献目标，引用须对应所问对象。')
        risk = state['intent'] == 'out_of_scope_or_risk'
        context = {'核心问题': state['session']['question'], '当前问题': state['question'],
                   '近期对话': history(state['session']),
                   '参考原文': [{k: e.get(k) for k in ('id', 'text', 'locator', 'workTitle', 'authorNames', 'layer')}
                             for e in generation_evidence(state)], '资料说明': notes}
        if risk:
            context['回答要求'] = '回应当前风险；只使用直接相关的用户陈述，优先必要的安全支持。'
        else:
            cast = state['session']['cast']
            context['卦象'] = {'本卦': cast['original']['name'], '变卦': cast['changed']['name'],
                '六爻值': cast['lines'], '爻名': [{6: '老阴', 7: '少阳', 8: '少阴', 9: '老阳'}[v] for v in cast['lines']],
                '动爻位置': [i + 1 for i in cast['changingIndices']]}
        if not risk:
            context['本轮阶段'] = '首次回应' if state['mode'] == 'initial' else '同题追问'
            context['回答要求'] = ('按所问文献目标查证；资料缺失就简短交代，不因缺失另加解卦或未经证据支持的文献知识。'
                if state['intent'] == 'source_request' else
                '从当前卦意回应本轮真正要解决的事，小事轻解、大事深谈；参考材料不是待讲解清单。')
        if risk:
            context.pop('核心问题', None)
            context.pop('参考原文', None)
            context.pop('资料说明', None)
        return [dict(role='system', content=RISK_SYSTEM if risk else SYSTEM), dict(role='user', content=dumps(context))]

    async def generate(state):
        messages = generation_messages(state)
        response, trace = await invoke(messages, state)
        return dict(response=response, trace=trace)

    def validate(state):
        try:
            value = json.loads(state['response'].get('content') or '')
        except (ValueError, TypeError):
            value = None
        answer, errors = validate_answer(value, generation_evidence(state), state['targets'], state['require_citations'])
        emit(dict(kind='validation', candidate=value, errors=errors, answer=answer,
                  evidence=generation_evidence(state), repairNumber=state['repairs']))
        return dict(answer=answer, errors=errors, next_step='finish' if answer or state['repairs'] >= 1 else 'repair',
                    trace=state['trace'] + [dict(step='validate', status='failed' if errors else 'passed', code=','.join(errors))])

    async def repair(state):
        # Field paths and actual limits let the model repair the observed 6/5
        # citation failure instead of blindly repeating INVALID_ANSWER_SCHEMA.
        instruction = ('修复以下 JSON 错误。本轮仅提供现实安全支持：citations 为空数组；不新增传统解释或原文缺失说明。'
                       if state['intent'] == 'out_of_scope_or_risk' else
                       '修复下列具体契约错误，同时保留对用户问题的直接回答。引文须在本轮证据中；明确文献目标未完成则说明不足，不用无关原文凑引用。保持正文连贯，允许同段讨论卦义与现实；claim 定位本段受证据支持的连续文字，不把建议当成引文证明。')
        messages = generation_messages(state) + [state['response'], dict(role='user', content=dumps(dict(
            instruction=instruction, errors=state['errors'])))]
        response, trace = await invoke(messages, state)
        return dict(response=response, trace=trace, repairs=state['repairs'] + 1)

    def finish(state):
        return dict(answer=state.get('answer') or fallback())

    builder = StateGraph(AgentState)
    for name, function in [('prepare', prepare), ('route', route), ('tools', tools_node),
                           ('generate', generate), ('validate', validate), ('repair', repair), ('finish', finish)]:
        builder.add_node(name, function)
    builder.add_edge(START, 'prepare')
    builder.add_conditional_edges('prepare', lambda s: s['next_step'], ['route', 'generate', 'finish'])
    builder.add_conditional_edges('route', lambda s: s['next_step'], ['route', 'tools', 'generate', 'finish'])
    builder.add_conditional_edges('tools', lambda s: s['next_step'], ['route', 'generate'])
    builder.add_edge('generate', 'validate')
    builder.add_conditional_edges('validate', lambda s: s['next_step'], ['repair', 'finish'])
    builder.add_edge('repair', 'validate')
    builder.add_edge('finish', END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=interrupt_before)
