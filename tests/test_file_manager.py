from __future__ import annotations

from pathlib import Path

from swe_mux import file_manager


def test_file_manager_commands_reveal_files_and_open_folders() -> None:
    folder = Path("C:/projects/example")
    file = folder / "notes.txt"

    assert file_manager.file_manager_command(folder, is_directory=True, platform="win32") == [
        "explorer.exe",
        "/n,",
        str(folder),
    ]
    assert file_manager.file_manager_command(file, is_directory=False, platform="win32") == [
        "explorer.exe",
        f"/select,{file}",
    ]
    assert file_manager.file_manager_command(file, is_directory=False, platform="darwin") == [
        "open",
        "-R",
        str(file),
    ]
    assert file_manager.file_manager_command(file, is_directory=False, platform="linux") == [
        "xdg-open",
        str(folder),
    ]


def test_new_windows_explorer_window_is_brought_forward(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    raised: list[int] = []
    monkeypatch.setattr(file_manager, "_windows_explorer_handles", lambda: [10, 20])
    monkeypatch.setattr(file_manager, "_bring_windows_window_forward", raised.append)

    file_manager._focus_launched_windows_explorer({10})

    assert raised == [20]
