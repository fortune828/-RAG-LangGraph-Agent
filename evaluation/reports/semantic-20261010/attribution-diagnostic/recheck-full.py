"""Revalidate the two rejected candidates with their recorded complete contract."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
from backend.graph import GRAPH_VERSION  # noqa: E402
from backend.validation import validate_answer  # noqa: E402

trace = Path(__file__).resolve().parents[1] / 'stress/paraphrase-stress-2-D.jsonl'
events = [json.loads(line) for line in trace.read_text().splitlines()]
prepared = next(event for event in events if event['kind'] == 'prepare')
results = []
for event in events:
    if event['kind'] != 'validation':
        continue
    answer, errors = validate_answer(event['candidate'], event['evidence'],
        prepared['targets'], prepared['requireCitations'])
    results.append(dict(eventId=event['eventId'], repairNumber=event['repairNumber'],
        oldErrors=event['errors'], newErrors=errors, mechanicalValid=answer is not None))
output = dict(kind='offline_full_contract_revalidation', graphVersion=GRAPH_VERSION,
    trace=str(trace.relative_to(ROOT)), traceSha256=hashlib.sha256(trace.read_bytes()).hexdigest(),
    validatorSha256=hashlib.sha256((ROOT/'backend/validation.py').read_bytes()).hexdigest(),
    targets=prepared['targets'], requireCitations=prepared['requireCitations'], results=results,
    boundary='Same recorded candidates/evidence/targets; no new model requests, no end-to-end rerun, no semantic certification.')
Path(__file__).with_name('full-contract.json').write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
print(json.dumps(results, ensure_ascii=False))
