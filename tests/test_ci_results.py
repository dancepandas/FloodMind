"""CI aggregation is fail-closed for every non-success state."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.check_ci_results import validate_results


@pytest.mark.parametrize('needs', [
    {}, [], None, 'success',
    {'test': {}}, {'test': None},
    *[{'test': {'result': result}} for result in ['failure', 'cancelled', 'skipped', 'unknown', '', None, True]],
])
def test_non_success_results_fail(needs):
    assert validate_results(needs)


def test_all_success_passes():
    assert validate_results({'test': {'result': 'success'}, 'contracts': {'result': 'success'}}) == []


@pytest.mark.parametrize('value, expected', [
    ('{"test":{"result":"success"}}', 0),
    ('{"test":{"result":"skipped"}}', 1),
    ('{}', 1), ('invalid json', 1), ('null', 1),
])
def test_cli_exit_codes(value, expected):
    script = Path(__file__).resolve().parents[1] / 'scripts/check_ci_results.py'
    result = subprocess.run([sys.executable, str(script)], env={**os.environ, 'NEEDS': value}, capture_output=True)
    assert result.returncode == expected
