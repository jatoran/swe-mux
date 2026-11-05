"""The UI language is an ordinary durable, hot-applied setting."""

import pytest

from swe_mux.config import Config, load_config, update_config


def test_language_persists_without_a_daemon_restart(tmp_path):
    path = tmp_path / "config.toml"
    config = Config(data_dir=tmp_path, config_path=path)
    hot, restart = update_config(config, {"ui_language": "zh-Hans"})
    assert hot == {"ui_language"}
    assert restart == set()
    assert load_config(path).ui_language == "zh-Hans"
    assert config.public_dict()["ui_language"] == "zh-Hans"


@pytest.mark.parametrize("language", ["fr", "zh-TW", "auto", None, 1, {}])
def test_language_rejects_unknown_or_malformed_values(tmp_path, language):
    config = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml")
    with pytest.raises(ValueError):
        update_config(config, {"ui_language": language})
    assert config.ui_language == ""


def test_messages_keep_ids_through_dataclass_copy_and_translate_only_marked_strings(monkeypatch):
    from copy import deepcopy

    from swe_mux import ui_messages as ui

    source = {**ui.catalog("en"), "test.greeting": "Hello {name}"}
    target = {"test.greeting": "你好，{name}"}
    monkeypatch.setattr(ui, "catalog", lambda language: source if language == "en" else target)
    message = deepcopy(ui.ui_message("test.greeting", name="项目"))
    assert str(message) == "Hello 项目"
    assert ui.localize_payload({"label": message, "status": "Hello 项目"}, "zh-Hans") == {
        "label": "你好，项目",
        "status": "Hello 项目",
    }
    assert isinstance(ui.error_text(ValueError(message)), ui.UiMessage)


@pytest.mark.asyncio
async def test_http_language_preference_and_context_are_request_scoped(monkeypatch):
    import json
    from types import SimpleNamespace

    from swe_mux import app_keys as keys
    from swe_mux import ui_messages as ui
    from swe_mux.http_support import json_response
    from swe_mux.server import localization_middleware

    source = {**ui.catalog("en"), "test.label": "Ready"}
    monkeypatch.setattr(
        ui, "catalog", lambda language: source if language == "en" else {"test.label": "就绪"}
    )
    request = SimpleNamespace(
        app={keys.CONFIG: SimpleNamespace(ui_language="zh-Hans")},
        headers={"Accept-Language": "en-US"},
    )

    async def handler(_request):
        return json_response({"label": ui.ui_message("test.label"), "state": "ready"})

    response = await localization_middleware(request, handler)
    assert json.loads(response.body) == {"label": "就绪", "state": "ready"}
    assert response.headers["Content-Language"] == "zh-Hans"
    assert "Accept-Language" in response.headers["Vary"]
    assert ui.UI_LOCALE.get() == "en"


def test_native_menu_follows_saved_preference_without_restarting(tmp_path, monkeypatch):
    from swe_mux import ui_messages as ui

    source = {**ui.catalog("en"), "test.open": "Open"}
    monkeypatch.setattr(
        ui, "catalog", lambda language: source if language == "en" else {"test.open": "打开"}
    )
    path = tmp_path / "config.toml"
    path.write_text('ui_language = "en"\n', encoding="utf-8")
    label = ui.native_label("test.open", path)
    assert label(None) == "Open"
    path.write_text('ui_language = "zh-Hans"\n', encoding="utf-8")
    assert label(None) == "打开"


@pytest.mark.asyncio
async def test_push_uses_current_language_without_changing_notification_identity(monkeypatch):
    import json
    from types import SimpleNamespace

    from swe_mux import push
    from swe_mux import ui_messages as ui

    source = {**ui.catalog("en"), "test.push": "Turn complete"}
    monkeypatch.setattr(
        ui, "catalog", lambda language: source if language == "en" else {"test.push": "本轮完成"}
    )
    delivered = []
    monkeypatch.setattr(
        push, "webpush", lambda **kwargs: delivered.append(json.loads(kwargs["data"]))
    )
    sender = push.PushSender(
        SimpleNamespace(vapid="test"),
        SimpleNamespace(),
        SimpleNamespace(),
        language=lambda: "zh-Hans",
    )
    sent = await sender._send(
        {"endpoint": "https://example.invalid/push", "keys": {}},
        {
            "title": ui.ui_message("test.push"),
            "body": ui.ui_message("test.push"),
            "category": "complete",
        },
    )
    assert sent
    assert delivered[0]["title"] == delivered[0]["body"] == "本轮完成"
    assert delivered[0]["type"] == delivered[0]["tag"] == "complete"


@pytest.mark.asyncio
async def test_localization_diagnostics_validate_the_whole_batch_before_logging(caplog):
    from swe_mux.routes.diagnostics import localization_diagnostic

    class Request(dict):
        async def json(self):
            return {
                "entries": [
                    {
                        "operation": "message_fallback",
                        "locale": "zh-Hans",
                        "message_id": "test.label",
                        "reason": "invalid_message",
                    },
                    {"operation": {}, "locale": "en", "secret": "must-not-be-logged"},
                ]
            }

    with pytest.raises(ValueError):
        await localization_diagnostic(Request())
    assert "must-not-be-logged" not in caplog.text
    assert "ui_localization" not in caplog.text


def test_backend_catalog_references_and_translation_placeholders():
    import ast
    import json
    import string
    from pathlib import Path

    root = Path(__file__).parents[1] / "src/swe_mux"
    english = json.loads((root / "assets/locales/en.json").read_text(encoding="utf-8"))
    chinese = json.loads((root / "assets/locales/zh-Hans.json").read_text(encoding="utf-8"))
    formatter = string.Formatter()
    assert english.keys() == chinese.keys()

    def fields(text):
        return sorted(
            (name, specification, conversion or "")
            for _, name, specification, conversion in formatter.parse(text)
            if name is not None
        )

    for key, text in chinese.items():
        assert key in english
        assert text.strip(), key
        assert fields(text) == fields(english[key]), key
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id not in {"ui_message", "native_label"} or not node.args:
                continue
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                assert node.args[0].value in english, (path, node.args[0].value)


def test_live_event_copy_follows_language_without_changing_protocol_or_stored_payload(monkeypatch):
    from types import SimpleNamespace

    from swe_mux import app_keys as keys
    from swe_mux import ui_messages as ui
    from swe_mux.routes.pty import browser_event

    source = {**ui.catalog("en"), "test.event": "Ready"}
    monkeypatch.setattr(
        ui, "catalog", lambda language: source if language == "en" else {"test.event": "就绪"}
    )
    config = SimpleNamespace(ui_language="en")
    request = SimpleNamespace(app={keys.CONFIG: config}, headers={})
    payload = {
        "type": "ready",
        "payload": {"message": ui.ui_message("test.event"), "user_text": "Ready"},
    }
    assert browser_event(request, payload)["payload"]["message"] == "Ready"
    config.ui_language = "zh-Hans"
    assert browser_event(request, payload) == {
        "type": "ready",
        "payload": {"message": "就绪", "user_text": "Ready"},
    }
    assert str(payload["payload"]["message"]) == "Ready"


def test_localized_response_validators_do_not_reuse_another_languages_body():
    from types import SimpleNamespace

    from swe_mux.http_support import revalidated_json
    from swe_mux.ui_messages import UI_LOCALE

    token = UI_LOCALE.set("zh-Hans")
    try:
        response = revalidated_json(
            SimpleNamespace(headers={"If-None-Match": '"version-1"'}),
            '"version-1"',
            lambda: {"label": "test"},
        )
        assert response.status == 200
        assert response.headers["ETag"] == '"version-1-ui-zh-Hans"'
        cached = revalidated_json(
            SimpleNamespace(headers={"If-None-Match": response.headers["ETag"]}),
            '"version-1"',
            lambda: (_ for _ in ()).throw(AssertionError("must not rebuild")),
        )
        assert cached.status == 304
    finally:
        UI_LOCALE.reset(token)


def test_every_declared_settings_validation_message_is_catalog_backed(tmp_path, monkeypatch):
    from swe_mux import config as config_module
    from swe_mux import ui_messages as ui

    for table in (
        config_module._RANGE_RULES,
        config_module._CHOICE_RULES,
        config_module._TEXT_RULES,
        config_module._PATTERN_RULES,
    ):
        assert all(isinstance(rule.message, ui.UiMessage) for rule in table)
    config = Config(data_dir=tmp_path, config_path=tmp_path / "config.toml", port=0)
    with pytest.raises(ValueError) as failure:
        config_module._validate(config)
    errors = failure.value.args[0]
    port_message = errors["port"]
    assert isinstance(port_message, ui.UiMessage)
    source = ui.catalog("en")
    monkeypatch.setattr(
        ui,
        "catalog",
        lambda language: source if language == "en" else {port_message.message_id: "端口超出范围"},
    )
    assert ui.localize_payload(errors, "zh-Hans")["port"] == "端口超出范围"


@pytest.mark.parametrize(
    ("message_id", "values", "expected"),
    [
        ("desktop.b13472970c", {}, "打开 swe-mux"),
        ("push.415d3bcb6c", {}, "智能体需要你的批准。"),
        ("land_queue.d782fb8506", {"value1": 12.4}, "验证通过，耗时 12 秒"),
        (
            "project_actions.6135b23cc7",
            {"value1": "input:branch"},
            "不支持的变量：${input:branch}",
        ),
    ],
)
def test_shipped_chinese_messages_render_without_fallback(message_id, values, expected, caplog):
    from swe_mux.ui_messages import render_message, ui_message

    message = ui_message(message_id, **values)
    assert render_message(message, "zh-Hans") == expected
    assert render_message(message, "en") == str(message)
    assert "ui_message_fallback" not in caplog.text
