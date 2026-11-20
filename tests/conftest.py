from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import pytest


@pytest.fixture(scope='session')
def yaml_chip_data_path() -> Traversable:
    return files('picpro') / 'data' / 'chip-data.d'


@pytest.fixture(scope='session')
def hex_file_path() -> Path:
    return Path(__file__).parent / 'data' / 'test.hex'
