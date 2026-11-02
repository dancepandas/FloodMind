"""Only explicitly registered documentation examples are executable."""
from pathlib import Path

import pytest

from scripts.check_doc_examples import extract_examples, load_examples, main


def test_extract_registered_example():
    assert extract_examples('```python contract-example=tool-basic\nx = 1\n```\n') == {"tool-basic": "x = 1\n"}


@pytest.mark.parametrize("text", [
    '```python contract-example=tool-basic\nx = 1\n',
    '```python contract-example=tool-basic\nx = 1\n```\n' * 2,
    '```python contract-example=\nx = 1\n```\n',
])
def test_extract_rejects_broken_markers(text):
    with pytest.raises(ValueError):
        extract_examples(text)


def write_document(root, text):
    path = root / 'docs/development/adding-a-tool.md'
    path.parent.mkdir(parents=True)
    path.write_text(text, encoding='utf-8')


@pytest.mark.parametrize("text", [
    '```python\nraise RuntimeError()\n```\n',
    '```python contract-example=typo\nx = 1\n```\n',
    '```python contract-example=tool-basic\ninvalid python !\n```\n',
])
def test_loader_rejects_missing_unknown_or_invalid_examples(tmp_path, text):
    write_document(tmp_path, text)
    with pytest.raises(ValueError, match='adding-a-tool.md'):
        load_examples(tmp_path)


def test_cli_executes_only_registered_blocks_in_temporary_cwd(tmp_path):
    write_document(tmp_path, '```python\nraise RuntimeError("ignored")\n```\n'
                   '```python contract-example=tool-basic\nfrom pathlib import Path\n'
                   'Path("temporary-result").write_text("ok")\n```\n')
    assert main(tmp_path) == 0
    assert not (tmp_path / 'temporary-result').exists()


def test_cli_reports_execution_failure(tmp_path):
    write_document(tmp_path, '```python contract-example=tool-basic\nraise RuntimeError("broken")\n```\n')
    assert main(tmp_path) == 1
