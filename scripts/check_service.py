"""Health and one real paid request. Run after starting Docker Compose."""
import json
import urllib.request

BASE = 'http://127.0.0.1:8000'
with urllib.request.urlopen(BASE + '/health', timeout=10) as response:
    print('HEALTH:', response.read().decode())
question = {'question':'Сколько воды использовано на A за 27–29 сентября 2026 включительно?',
            'user_id':'demo-operator', 'session_id':'live-demo'}
req = urllib.request.Request(BASE+'/ask', data=json.dumps(question).encode(),
                             headers={'Content-Type':'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=240) as response:
    result = json.load(response)
print(json.dumps(result, ensure_ascii=False, indent=2))
print('Ожидаемый эталон: 400 м³. Проверьте ответ и трейс, не исправляйте его вручную.')
