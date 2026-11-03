"""Checks expose trained outputs, with coverage and uncertainty on every metric."""
from .catalog import EXAMS
from .learning.runtime import get_engine


def sample(target,state,treatments,rng,intake):
    return get_engine().sample(target,state,treatments,rng,intake)


def report_text(target,items):
    return EXAMS[target]['name']+'：'+'；'.join(f"{i['name']} {i['value']} {i['unit'] or ''}" for i in items)
