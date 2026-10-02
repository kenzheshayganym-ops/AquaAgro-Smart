from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain.agents import create_agent
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from aquaagro.config import Settings
from aquaagro.store import Store
from aquaagro.tools import build_tools
from aquaagro.agent import AgentService
from aquaagro.api import create_app
from aquaagro.tracing import start_tracker
from aquaagro.prompts import AGENT_PROMPT

DATA = Path(__file__).resolve().parents[1] / 'data'

class Tracker:
    def __init__(self, auth=True, delivered=True):
        self.auth, self.delivered = auth, delivered
        self.closed = False
        self.scores = []
        self.updates = []
        self.api = SimpleNamespace(observations=SimpleNamespace(get_many=self.get_many))
    def auth_check(self): return self.auth
    def create_trace_id(self): return 'a'*32
    def start_as_current_observation(self, **kwargs): return nullcontext(self)
    def update(self, **kwargs): self.updates.append(kwargs)
    def flush(self): pass
    def shutdown(self): self.closed = True
    def get_many(self, **kwargs): return SimpleNamespace(data=[{'id':'test'}] if self.delivered else [])
    def create_score(self, **kwargs): self.scores.append(kwargs)

class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs): return self

@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / 'state/aquaagro.sqlite', DATA)

@pytest.fixture
def settings(tmp_path):
    return Settings('test-openai', 'test-public', 'test-secret', 'https://cloud.langfuse.com',
                    'gpt-4.1-mini-2025-04-14', 'test', tmp_path/'db.sqlite', DATA, True)

def test_snapshot_and_inclusive_boundaries(store):
    tools = {t.name:t for t in build_tools(store, True)}
    assert tools['reading_calculation'].invoke({'field_id':'G','metric':'moisture','operation':'range'})['value']=='в_диапазоне'
    assert tools['reading_calculation'].invoke({'field_id':'D','metric':'moisture','operation':'value'})['type']=='numeric'
    assert tools['reading_calculation'].invoke({'field_id':'E','metric':'moisture','operation':'value'})['type']=='refusal'
    assert tools['reading_calculation'].invoke({'field_id':'E','metric':'moisture','on_date':'2026-09-30'})['type']=='numeric'
    assert tools['reading_calculation'].invoke({'field_id':'F','metric':'moisture'})['type']=='refusal'
    assert tools['reading_calculation'].invoke({'field_id':'H','metric':'moisture'})['type']=='refusal'

def test_calculations_missing_and_zero(store):
    water = {t.name:t for t in build_tools(store)}['water_calculation']
    args={'field_ids':['A'],'start_date':'2026-09-27','end_date':'2026-09-29'}
    assert water.invoke(args)['value']==400
    assert water.invoke({**args,'operation':'per_hectare'})['value']==20
    assert water.invoke({**args,'field_ids':['C','A'],'operation':'difference'})['value']==200
    assert water.invoke({**args,'start_date':'2026-09-26'})['type']=='refusal'
    assert water.invoke({'field_ids':['G'],'start_date':'2026-09-29','end_date':'2026-09-29'})['value']==0

def test_memory_survives_reopen_and_isolates_users(store):
    record={'request_id':'one','seconds':1}
    store.save_exchange('alice','same-session','question','answer',record)
    reopened=Store(store.db_path,DATA)
    assert len(reopened.session_history('alice','same-session'))==2
    assert reopened.session_history('bob','same-session')==[]
    assert reopened.session_history('alice','other-session')==[]
    assert reopened.usage('one')==record

def test_memory_last_six_exchanges(store):
    for i in range(8):
        store.save_exchange('u','s',f'q{i}',f'a{i}',{'request_id':str(i)})
    rows=store.session_history('u','s')
    assert len(rows)==12
    assert rows[0]['content']=='q2'
    assert rows[-1]['content']=='a7'

def test_snapshot_tampering_detected(store):
    with store.connect() as conn:
        conn.execute("UPDATE source_records SET payload='{}' WHERE kind='fields' AND position=0")
    with pytest.raises(RuntimeError): store.check_database()
    with pytest.raises(RuntimeError): Store(store.db_path,DATA)

def test_startup_auth_failure(settings):
    tracker=Tracker(auth=False)
    with pytest.raises(RuntimeError, match='Langfuse startup check failed'):
        start_tracker(settings,client_factory=lambda **kw:tracker,wait=lambda t:None)
    assert tracker.closed

def test_startup_requires_actual_delivery(settings):
    tracker=Tracker(delivered=False)
    with pytest.raises(RuntimeError):
        start_tracker(settings,client_factory=lambda **kw:tracker,wait=lambda t:None)
    assert tracker.closed
    tracker=Tracker()
    assert start_tracker(settings,client_factory=lambda **kw:tracker)[1]=='a'*32

def make_service(store, settings):
    final={'type':'numeric','value':400,'unit':'м³','explanation':'Сумма записей water-A-27, water-A-28, water-A-29.'}
    model=ToolModel(responses=[
        AIMessage(content='',tool_calls=[{'name':'water_calculation','args':{
            'field_ids':['A'],'start_date':'2026-09-27','end_date':'2026-09-29','operation':'sum'},
            'id':'call-test','type':'tool_call'}]),
        AIMessage(content=json.dumps(final,ensure_ascii=False)),
    ])
    real_graph=create_agent(model=model,tools=build_tools(store,True),system_prompt=AGENT_PROMPT)
    return AgentService(settings,store,Tracker(),agent=real_graph,
                        handler_factory=lambda **kw:BaseCallbackHandler())

def test_real_graph_tools_api_and_metadata(store,settings):
    service=make_service(store,settings)
    with TestClient(create_app(runtime_factory=lambda:(service,'a'*32))) as client:
        assert client.get('/health').json()['tracing_startup_verified'] is True
        r=client.post('/ask',json={'question':'Сумма A за 27–29 сентября 2026?','user_id':'alice','session_id':'one'})
        assert r.status_code==200
        body=r.json()
        assert body['answer']['value']==400
        assert body['usage']['tool_calls']==1
        assert len(store.session_history('alice','one'))==2
        assert service.tracker.scores[0]['value']==1
        assert client.post('/ask',json={'question':' '}).status_code==422
        assert client.post('/ask',json={'question':'hello','session_id':'../bad'}).status_code==422
        service.lock.acquire()
        try: assert client.post('/ask',json={'question':'hello'}).status_code==429
        finally: service.lock.release()
    assert service.tracker.closed

def test_provider_failure_returns_safe_error(store,settings):
    class Broken:
        def invoke(self,*a,**kw): raise ValueError('do-not-expose-this')
    service=AgentService(settings,store,Tracker(),agent=Broken(),handler_factory=lambda **kw:BaseCallbackHandler())
    with TestClient(create_app(runtime_factory=lambda:(service,'a'*32))) as client:
        r=client.post('/ask',json={'question':'test'})
        assert r.status_code==502
        assert 'do-not-expose-this' not in r.text
        assert store.session_history('demo-operator','demo-session')==[]

def test_prompt_and_tools_build_without_network(store,settings):
    service=AgentService(settings,store,Tracker())
    assert len(service.tools)==5
    assert service.settings.freshness is True
