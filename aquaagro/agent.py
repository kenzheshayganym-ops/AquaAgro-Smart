"""Measured agent packaged as a service; prompt/tool semantics are preserved."""
import json
import math
import threading
import time
import uuid
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langfuse import propagate_attributes
from langfuse.langchain import CallbackHandler
from .prompts import AGENT_PROMPT
from .tools import build_tools


def format_valid(answer):
    if not isinstance(answer, dict) or not {'type','value','unit','explanation'} <= answer.keys():
        return False
    if not isinstance(answer['explanation'], str) or not answer['explanation'].strip():
        return False
    kind, value, unit = answer['type'], answer['value'], answer['unit']
    if kind == 'numeric':
        return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) \
            and isinstance(unit,str) and bool(unit.strip())
    if kind == 'refusal':
        return value == 'отказ' and unit is None
    if kind == 'status':
        return isinstance(value,str) and bool(value.strip()) and unit is None
    return False


class AgentService:
    def __init__(self, settings, store, tracker, *, agent=None, handler_factory=CallbackHandler):
        self.settings, self.store, self.tracker = settings, store, tracker
        self.handler_factory = handler_factory
        self.tools = build_tools(store, settings.freshness)
        self.agent = agent if agent is not None else create_agent(
            model=ChatOpenAI(model=settings.model, api_key=settings.openai_key,
                             temperature=0, max_tokens=700, timeout=60, max_retries=2),
            tools=self.tools, system_prompt=AGENT_PROMPT,
        )
        # Single process, serialized requests: prevents history races in this small prototype.
        self.lock = threading.Lock()

    def ask(self, question, user_id, session_id):
        if not self.lock.acquire(blocking=False):
            raise BlockingIOError('Another request is in progress')
        record = {'request_id': uuid.uuid4().hex, 'trace_id': self.tracker.create_trace_id(),
                  'user_id': user_id, 'session_id': session_id, 'model': self.settings.model,
                  'variant': 'step2plus-service', 'prompt_tokens': 0, 'completion_tokens': 0,
                  'model_calls': 0, 'tool_calls': 0, 'error_type': None}
        started = time.perf_counter()
        try:
            self.store.check_database()
            messages = self.store.session_history(user_id, session_id)
            messages.append({'role':'user','content':question})
            trace_session = 'session-' + uuid.uuid5(uuid.NAMESPACE_URL,
                self.store.memory_key(user_id, session_id)).hex
            tags = ['aquaagro', self.settings.environment, record['variant']]
            with propagate_attributes(user_id=user_id, session_id=trace_session, tags=tags):
                with self.tracker.start_as_current_observation(
                    name='step2plus-service-request', as_type='span',
                    trace_context={'trace_id': record['trace_id']}, input=question,
                    metadata={'request_id':record['request_id'], 'variant':record['variant'],
                              'data_hash':self.store.data_hash, 'eval_hash':self.store.eval_hash,
                              'freshness_guard':self.settings.freshness},
                ) as span:
                    result = self.agent.invoke({'messages':messages}, config={
                        'callbacks':[self.handler_factory(public_key=self.settings.lf_public)],
                        'recursion_limit':12,
                        'metadata':{
                            'langfuse_session_id':trace_session, 'langfuse_user_id':user_id,
                            'langfuse_tags':tags, 'langfuse_trace_name':'step2plus-service-request',
                        },
                    })
                    new_messages = result['messages'][len(messages):]
                    for message in new_messages:
                        usage = getattr(message, 'usage_metadata', None)
                        if usage:
                            record['prompt_tokens'] += usage.get('input_tokens', 0)
                            record['completion_tokens'] += usage.get('output_tokens', 0)
                            record['model_calls'] += 1
                        record['tool_calls'] += len(getattr(message, 'tool_calls', []) or [])
                    raw = result['messages'][-1].content
                    if not isinstance(raw,str):
                        raise ValueError('Expected a string response')
                    def reject_constant(value):
                        raise ValueError('Nonfinite JSON number')
                    answer = json.loads(raw, parse_constant=reject_constant)
                    if not isinstance(answer,dict):
                        raise ValueError('Expected a JSON object')
                    valid = format_valid(answer)
                    record['format_valid'] = valid
                    record['seconds'] = time.perf_counter()-started
                    # No auto-correction of answers after evaluation.
                    span.update(output=answer, metadata={'format_valid':valid})
                    self.tracker.create_score(trace_id=record['trace_id'], name='format_valid',
                                              value=float(valid), data_type='NUMERIC')
                    self.store.save_exchange(user_id, session_id, question, raw, record)
            self.tracker.flush()
            return {'answer':answer, 'trace_id':record['trace_id'], 'request_id':record['request_id'],
                    'session_id':session_id, 'format_valid':valid, 'usage':{
                        'prompt_tokens':record['prompt_tokens'],
                        'completion_tokens':record['completion_tokens'],
                        'model_calls':record['model_calls'], 'tool_calls':record['tool_calls']},
                    'seconds':record['seconds']}
        except Exception as exc:
            record['error_type'] = type(exc).__name__
            record['seconds'] = time.perf_counter()-started
            self.store.record_failure(record)
            raise RuntimeError('Agent request failed: ' + type(exc).__name__) from None
        finally:
            self.lock.release()
