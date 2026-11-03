from __future__ import annotations

import threading
from dataclasses import replace

from ehrworld.llm import APIConfig, JSONClient
from .policy import LLMDoctor, DemoDoctor
from .learning.runtime import LearnedDynamics
from .outcome import LLMOutcome, DemoOutcome

_LIMIT=threading.BoundedSemaphore(4)


class LimitedClient(JSONClient):
    def request(self,*args,**kwargs):
        with _LIMIT:return super().request(*args,**kwargs)


class Models:
    def __init__(self,request,client_factory=LimitedClient):
        self.clients={}
        self.dynamics=LearnedDynamics()
        if request.backend=='demo':
            self.policy,self.outcome=DemoDoctor(),DemoOutcome()
        else:
            config=APIConfig.from_env()
            try:
                for role in ('policy','outcome'):
                    self.clients[role]=client_factory(replace(config,model=getattr(request,role+'_model') or request.model))
            except Exception:
                self.close();raise
            self.policy=LLMDoctor(self.clients['policy']);self.outcome=LLMOutcome(self.clients['outcome'])

    def statistics(self):
        return {role:client.statistics() for role,client in self.clients.items()}

    def close(self):
        for client in self.clients.values():client.close()
