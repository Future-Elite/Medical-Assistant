from __future__ import annotations

import pytest

from ehrworld.data import construct_episode
from ehrworld.demo import synthetic_admissions


@pytest.fixture
def raw():
    return next(synthetic_admissions())


@pytest.fixture
def episode(raw):
    return construct_episode(raw)[0]
