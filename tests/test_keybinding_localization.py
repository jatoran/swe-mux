"""Shortcut presentation translates independently of persisted bindings."""

import pytest

from swe_mux import keybindings, keychords, keymaps
from swe_mux.ui_messages import UiMessage, localize_payload


def test_all_command_labels_and_preset_descriptions_are_catalog_messages():
    assert all(isinstance(label, UiMessage) for _, label, _ in keybindings.KEYBINDING_COMMANDS)
    assert all(isinstance(title, UiMessage) for _, _, title in keybindings.COMMAND_GROUPS)
    for preset in keymaps.presets().values():
        assert isinstance(preset.description, UiMessage)
        assert not preset.warning or isinstance(preset.warning, UiMessage)


def test_chinese_keymaps_preserve_chords_ids_and_resolution():
    for preset_id in keymaps.preset_ids():
        rules = keymaps.preset_rules(preset_id)
        resolution = keybindings.resolve(rules, host="browser", platform="win")
        assert localize_payload(resolution.bindings, "zh-Hans") == resolution.bindings
        source = keymaps.presets()[preset_id]
        translated = localize_payload(
            {"description": source.description, "id": source.id}, "zh-Hans"
        )
        assert translated["id"] == source.id
        assert translated["description"] != str(source.description)


def test_nested_conflict_reasons_translate_and_custom_text_stays_literal():
    warnings = keychords.chord_warnings("ctrl+f", host="browser", platform="win")
    translated = localize_payload([warning.as_dict() for warning in warnings], "zh-Hans")
    assert any("浏览器" in row["message"] for row in translated)
    assert (
        localize_payload({"note": "my custom Ctrl+F note"}, "zh-Hans")["note"]
        == "my custom Ctrl+F note"
    )


@pytest.mark.parametrize('reference', ['missing.message', 42, ['not-an-id']])
def test_invalid_preset_message_is_a_localizable_validation_error(reference):
    with pytest.raises(ValueError) as error:
        keymaps._presentation_text({'title_message': reference}, 'title')
    assert isinstance(error.value.args[0], UiMessage)
    assert '未知消息' in localize_payload(error.value.args[0], 'zh-Hans')


def test_custom_preset_metadata_remains_literal():
    assert keymaps._presentation_text({'title': 'my own title'}, 'title') == 'my own title'
