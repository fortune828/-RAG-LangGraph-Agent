"""Four bounded live capability probes; stores no credentials or reasoning text."""
import asyncio
import argparse
import hashlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]


def visible(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key == 'reasoning_content':
                result['reasoningContentMetadata'] = {
                    'characters': len(item or ''),
                    'sha256': hashlib.sha256((item or '').encode()).hexdigest(),
                }
            else:
                result[key] = visible(item)
        return result
    if isinstance(value, list):
        return [visible(item) for item in value]
    return value


def save(name, data):
    (OUT / name).write_text(json.dumps(visible(data), ensure_ascii=False, indent=2) + '\n')


async def main():
    load_dotenv(ROOT / '.env.local')
    key = os.environ['DEEPSEEK_API_KEY']
    async with httpx.AsyncClient(timeout=90, headers={'Authorization': f'Bearer {key}'}) as client:
        models = await client.get('https://api.deepseek.com/models')
        save('models.json', {'observedAt': datetime.now(timezone.utc).isoformat(),
                             'httpStatus': models.status_code, 'response': models.json()})
        print(json.dumps({'httpStatus': models.status_code,
                          'modelIds': [m['id'] for m in models.json().get('data', [])]}))
        results = []
        for model in ('deepseek-flash', 'deepseek-v4-pro'):
            for thinking in ('disabled', 'enabled'):
                request = {'model': model, 'max_tokens': 1024, 'thinking': {'type': thinking},
                           'messages': [{'role': 'system', 'content': 'Return only a JSON object with the answer.'},
                                        {'role': 'user', 'content': 'What is 17 plus 28? Use the field answer.'}],
                           'response_format': {'type': 'json_object'}}
                if thinking == 'enabled':
                    request['reasoning_effort'] = 'low'
                started = time.perf_counter()
                row = {'observedAt': datetime.now(timezone.utc).isoformat(), 'request': request}
                try:
                    response = await client.post('https://api.deepseek.com/chat/completions', json=request)
                    row.update(httpStatus=response.status_code, response=response.json())
                except (httpx.RequestError, ValueError) as exc:
                    row['error'] = type(exc).__name__
                row['latencyMs'] = round((time.perf_counter() - started) * 1000)
                results.append(row)
                save(f'{model}-{thinking}.json', row)
                data = row.get('response', {})
                print(json.dumps({'requestedModel': model, 'thinking': thinking,
                                  'httpStatus': row.get('httpStatus'), 'returnedModel': data.get('model'),
                                  'latencyMs': row['latencyMs'], 'usage': data.get('usage'),
                                  'finishReason': (data.get('choices') or [{}])[0].get('finish_reason'),
                                  'error': row.get('error')}, ensure_ascii=False))
        save('probes.json', results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    OUT = parser.parse_args().output.resolve()
    if any(OUT.glob('*.json')):
        raise SystemExit('Output already contains results; choose a fresh --output directory.')
    OUT.mkdir(parents=True, exist_ok=True)
    asyncio.run(main())
