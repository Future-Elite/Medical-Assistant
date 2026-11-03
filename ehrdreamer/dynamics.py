"""Trained dynamics facade. Old hand-written clinical equations were removed."""
from .learning.runtime import LearnedState, LearnedDynamics, ModelArtifactError, get_engine, snapshot


def initialize(intake, rng, priors=None):
    return get_engine().initial(intake,rng)


def advance(state, treatments, start, end, rng, intake):
    return get_engine().advance(state,treatments,start,end,rng,intake)
