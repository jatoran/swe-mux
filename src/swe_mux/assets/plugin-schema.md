# swe-mux plugin manifest v1

`swe-mux-plugin.toml` declares inert metadata and external-process contributions.
Validate a directory without executing it with `swemux plugin validate PATH`.

```toml
manifest_version = 1
id = "publisher.plugin"
name = "Plugin"
version = "1.0.0"
min_swe_mux_version = "0.1.5"
description = "What this plugin does."
author = "Publisher"
license = "MIT"
homepage = "https://example.com"
platforms = ["windows", "linux", "macos"]
architectures = ["x86_64", "arm64"]
requires = ["plugin.actions.v1"]
permissions = ["projects.read"]
runtime_requirements = ["python>=3.10"]

[[actions]]
id = "run"
title = "Run utility"
description = "Visible action description."
contexts = ["global", "project", "session"]
command = ["python", "plugin.py"]
cwd = "."
env = { MODE = "action" }
timeout_seconds = 60
platforms = ["windows", "linux", "macos"]
```

Contribution tables are `actions`, `panes`, `events`, `startup`, and `link_handlers`.
Each non-empty contribution family must appear in `requires` as `plugin.actions.v1`, `plugin.panes.v1`, `plugin.events.v1`, `plugin.startup.v1`, or `plugin.links.v1`.

Permissions are `projects.read`, `sessions.read`, `sessions.control`, `terminal.write`, `notifications.write`, and `plugins.self`.
Permissions scope the callback endpoint and do not sandbox the plugin process.

Pane fields add `placement = "tab" | "split" | "popup"` and use Project context for launch.
Event fields add `on`, optional string-valued `match`, and `rate_limit_seconds`.
Startup entries are bounded one-shot commands.
Link handlers declare `pattern` and the ID of an action in the same manifest.

Runtime commands receive `SWEMUX_PLUGIN_ID`, `SWEMUX_PLUGIN_VERSION`, `SWEMUX_PLUGIN_ROOT`, `SWEMUX_PLUGIN_CONFIG_DIR`, `SWEMUX_PLUGIN_STATE_DIR`, `SWEMUX_PLUGIN_CONTRIBUTION_KIND`, `SWEMUX_PLUGIN_CONTRIBUTION_ID`, `SWEMUX_PLUGIN_CONTEXT_JSON`, `SWEMUX_PLUGIN_TOKEN`, `SWEMUX_API_URL`, and `SWEMUX_BIN_PATH`.

The callback is `POST $SWEMUX_API_URL` with `Authorization: Bearer $SWEMUX_PLUGIN_TOKEN` and a JSON `operation`.
Supported operations are `projects.list`, `sessions.list`, `terminal.write`, `session.stop`, `notify`, and `self.describe`, each gated by its corresponding manifest permission.
