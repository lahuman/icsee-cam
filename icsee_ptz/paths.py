"""Explicit runtime locations, independent of Python installation paths."""
import os
from pathlib import Path


def project_root():
    return Path(os.environ.get('ICSEE_PROJECT_DIR', Path.cwd())).resolve()


def data_root():
    return Path(os.environ.get('ICSEE_DATA_DIR', project_root() / 'data')).resolve()


def config_root():
    return Path(os.environ.get('ICSEE_CONFIG_DIR', project_root() / 'config')).resolve()


def runtime_root():
    return Path(os.environ.get('ICSEE_RUNTIME_DIR', data_root() / 'runtime')).resolve()
