from __future__ import annotations

import pytest

from swe_mux.spawn_contract import SpawnRequest


def test_spawn_contract_normalizes_structured_fields() -> None:
    request = SpawnRequest.parse(
        {"backend": "shell", "exe": "pwsh", "exe_args": ["-NoLogo"], "project_id": "dev"}
    )
    assert request.executable == "pwsh"
    assert request.argv == ("-NoLogo",)
    assert request.project_id == "dev"


@pytest.mark.parametrize(
    "body,field",
    [
        ({"backend": "claude", "profile_id": "pwsh"}, "profile_id"),
        ({"profile_id": "pwsh", "executable": "pwsh"}, "executable"),
        ({"argv": "--bad"}, "argv"),
        ({"backend": "shell"}, "project_id"),
        ({"backend": "shell", "project_id": "dev", "cwd": "elsewhere"}, "cwd"),
        ({"surprise": True}, "surprise"),
    ],
)
def test_spawn_contract_rejects_ambiguous_or_untyped_requests(
    body: dict[str, object], field: str
) -> None:
    with pytest.raises(ValueError) as error:
        SpawnRequest.parse(body)
    assert field in error.value.args[0]
