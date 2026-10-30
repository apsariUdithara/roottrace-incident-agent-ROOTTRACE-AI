"""The sandbox CLI logs itself in on a server, and never touches an existing profile.

On the free Render deploy there was no `contree auth` profile, so every sandbox call failed and
each fix came back "untested"."""

import asyncio

from app.sandbox import nebius


class FakeProcess:
    returncode = 0

    async def communicate(self):
        return b"", b""


def run_login(monkeypatch, tmp_path, *, key=True, project=True, profile=False) -> list[tuple]:
    calls = []

    async def fake_exec(*args, **kwargs):
        calls.append(args)
        return FakeProcess()

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    for name, present in (("NEBIUS_API_KEY", key), ("NEBIUS_AI_PROJECT", project)):
        if present:
            monkeypatch.setenv(name, "set")
        else:
            monkeypatch.delenv(name, raising=False)
    if profile:
        nebius.profile_path().parent.mkdir(parents=True)
        nebius.profile_path().write_text("[default]\n")
    monkeypatch.setattr(nebius.asyncio, "create_subprocess_exec", fake_exec)
    asyncio.run(nebius.ensure_login())
    return calls


def test_a_server_logs_in_from_its_environment(monkeypatch, tmp_path):
    [call] = run_login(monkeypatch, tmp_path)
    assert call[-2:] == ("contree_cli", "auth")


def test_an_existing_profile_is_left_alone(monkeypatch, tmp_path):
    assert run_login(monkeypatch, tmp_path, profile=True) == []


def test_without_a_project_id_it_does_not_try(monkeypatch, tmp_path):
    assert run_login(monkeypatch, tmp_path, project=False) == []
