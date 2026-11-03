"""Per-request model selection; credentials and endpoint remain server-only."""
from typing import Literal

ModelName = Literal['DeepSeek-V4.1-Flash', 'GLM-5.3', 'GLM-5.3-Flash', 'MiMo-V2.6-Flash', 'Qwen3.8-27B']
MODEL_NAMES = ('DeepSeek-V4.1-Flash', 'GLM-5.3', 'GLM-5.3-Flash', 'MiMo-V2.6-Flash', 'Qwen3.8-27B')
DEFAULT_MODEL = 'Qwen3.8-27B'


def thinking_policy(model):
    if model in ('GLM-5.3', 'GLM-5.3-Flash'):
        return {'enabled': True, 'effort': 'low', 'label': '最低强度思考',
                'note': '官方接口不支持彻底关闭思考，使用 low，避免默认的高强度。'}
    return {'enabled': False, 'effort': None, 'label': '关闭思考', 'note': '请求关闭思考，实际支持情况由模型网关决定。'}


def request_options(model):
    if model in ('GLM-5.3', 'GLM-5.3-Flash'):
        return {'extra_body': {'thinking': {'type': 'enabled'}}, 'reasoning_effort': 'low'}
    if model.startswith(('DeepSeek', 'MiMo')):
        return {'extra_body': {'thinking': {'type': 'disabled'}}}
    return {'extra_body': {'enable_thinking': False, 'chat_template_kwargs': {'enable_thinking': False}}}


def model_catalog():
    return {'default_model': DEFAULT_MODEL,
            'items': [{'id': name, 'name': name, 'thinking_policy': thinking_policy(name)} for name in MODEL_NAMES]}
