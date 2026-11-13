import threading
from dataclasses import replace
from ehrworld.llm import APIConfig, JSONClient
from .dynamics import LLMDynamics
from .planner import LLMPlanner

_LIMIT = threading.BoundedSemaphore(4)


class LimitedClient(JSONClient):
    def request(self,*args,**kwargs):
        with _LIMIT:
            return super().request(*args,**kwargs)


class DualModels:
    def __init__(self,request,client_factory=LimitedClient):
        if request.backend != 'api':
            raise ValueError('双LLM引擎需要API配置，不提供伪装成LLM的离线动力学。')
        self.clients = {}
        config = APIConfig.from_env()
        try:
            for role in ('policy','dynamics','outcome'):
                self.clients[role] = client_factory(replace(config,model=getattr(request,role+'_model') or request.model))
            self.policy = LLMPlanner(self.clients['policy'])
            self.dynamics = LLMDynamics(self.clients['dynamics'])
            self.outcome = self.clients['outcome']
        except Exception:
            self.close()
            raise

    def statistics(self):
        return {role:{k:v for k,v in client.statistics().items() if k!='base_url'} for role,client in self.clients.items()}

    def close(self):
        for client in self.clients.values():
            client.close()
