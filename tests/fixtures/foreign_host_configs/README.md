# Foreign-host `config.toml` corpus

Three realistic states a `config.toml` reaches when it outlives the host that
wrote it, kept as files rather than as strings inside one test so more than one
test can consume them.
Load them through `tests/support/foreign_host_configs.py`, which copies a
fixture into a `tmp_path` before handing the path to `load_config` - the loader
writes healed values back, so a test that pointed at the checkout's copy would
edit the fixture underneath the next test.

| file | what it is |
| --- | --- |
| `windows_authored.toml` | written by a Windows build, loaded on POSIX. The `shell_exe`/`harness_exe` pair is the exact content measured on a live WSL Ubuntu daemon on 2026-08-28, where the Run menu could not launch Claude and provider login died with `No such file or directory: 'codex.exe'`. |
| `posix_authored.toml` | the mirror: written by a Linux build, loaded on Windows. Its paths are POSIX-absolute, which Windows cannot start and `_validate` refuses as non-absolute. |
| `ancient_schema.toml` | predates `harness.host_executable` (2026-08-17) *and* the `harness_exe` map, so its Windows-shaped executables arrive through the per-harness `<name>_exe` legacy keys. This is the shape the reported install actually upgraded from - its `config.toml.bak` was dated 2026-08-16. |

Two properties every fixture keeps, because they are what makes it evidence
rather than decoration:

- **Only the host-shaped values are foreign.** Everything else is an ordinary
  value, so a test asserting "nothing foreign survives" is asserting about the
  loader rather than about a file written to make a point.
- **They stay schema-versioned as written**, and are never renumbered to the
  current `SCHEMA_VERSION`. A fixture bumped to today's schema stops exercising
  the migrations it was collected for, silently.
