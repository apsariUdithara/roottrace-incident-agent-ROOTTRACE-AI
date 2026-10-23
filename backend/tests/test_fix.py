"""The Fix Generator's guard: a patch may add tests but never change existing ones."""

from app.agent.fix import FileChange, example_test, touches_existing_tests


def make_repo(tmp_path):
    """A tiny repository with one source file and one existing test file."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "config.py").write_text("POOL = 100\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_config.py").write_text("def test_pool(): ...\n")
    return tmp_path


def test_editing_an_existing_test_file_is_blocked(tmp_path):
    repo = make_repo(tmp_path)
    patch = [
        FileChange(path="src/config.py", content="POOL = 20\n"),
        FileChange(path="tests/test_config.py", content="def test_pool(): assert True\n"),
    ]
    assert touches_existing_tests(repo, patch) == ["tests/test_config.py"]


def test_a_new_test_file_and_a_source_change_are_allowed(tmp_path):
    repo = make_repo(tmp_path)
    patch = [
        FileChange(path="src/config.py", content="POOL = 20\n"),
        FileChange(path="tests/test_regression_pool.py", content="def test_new(): assert True\n"),
    ]
    assert touches_existing_tests(repo, patch) == []


def test_the_example_test_is_the_one_about_the_changed_file(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "tests" / "test_other.py").write_text("def test_other(): ...\n")
    example = example_test(repo, ["src/config.py"])
    assert example.startswith("--- tests/test_config.py") and "def test_pool" in example
