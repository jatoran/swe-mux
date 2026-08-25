"""The Preview proxy rewrites module specifiers and nothing that merely looks like one.

A Preview serves someone else's app through the mux origin, so every root-absolute module
specifier has to be prefixed or it 404s. The rewrite used to be lexical - it matched the
text `from "/…"` wherever it appeared - which meant an app's own strings, comments,
template literals, and JSON blocks were rewritten too, silently, on the way to the browser
(`.docs/development/CODE_QUALITY_AUDIT_2026-08-23.md` finding 21). S12.5 moved it onto the
tree-sitter grammar that is already a dependency, so a specifier is found by *being* one.

These are mostly negative fixtures, because the positive cases were already covered and
the whole defect was in what else got rewritten.
"""

from __future__ import annotations

from swe_mux.preview_transport import (
    _JS_SPECIFIERS,
    rewrite_preview_html,
    rewrite_preview_javascript,
)

PREFIX = "/preview/preview-id/"


def rewritten(source: str) -> str:
    return rewrite_preview_javascript(source.encode("utf-8"), PREFIX).decode("utf-8")


def test_the_grammar_is_available_here() -> None:
    # Every other test in this file would pass on the lexical fallback for the wrong
    # reason if the grammars were missing, so say so once, loudly.
    assert _JS_SPECIFIERS.spans(b"import a from '/b.js'") is not None


def test_every_specifier_position_is_rewritten() -> None:
    source = (
        'import client from "/@vite/client";\n'
        'import "/side-effect.js";\n'
        'export { a } from "/re-export.js";\n'
        'export * from "/star.js";\n'
        'const lazy = import("/src/lazy.ts");\n'
    )
    assert rewritten(source) == (
        f'import client from "{PREFIX}@vite/client";\n'
        f'import "{PREFIX}side-effect.js";\n'
        f'export {{ a }} from "{PREFIX}re-export.js";\n'
        f'export * from "{PREFIX}star.js";\n'
        f'const lazy = import("{PREFIX}src/lazy.ts");\n'
    )


def test_an_ordinary_string_that_reads_like_an_import_is_left_alone() -> None:
    # The failure this replaces: an app that renders its own documentation, or posts a
    # code sample to an API, shipped a rewritten copy of it instead.
    source = (
        'const sample = \'import x from "/src/main.ts"\';\n'
        'const path = "/api/from/here";\n'
        'log("import(\\"/not/a/module\\")");\n'
    )
    assert rewritten(source) == source


def test_a_comment_is_left_alone() -> None:
    source = (
        '// import client from "/@vite/client"\n'
        "/* export { a } from \"/re-export.js\"\n"
        '   import("/src/lazy.ts") */\n'
        "const value = 1;\n"
    )
    assert rewritten(source) == source


def test_a_template_literal_is_left_alone_including_its_substitutions() -> None:
    source = (
        "const snippet = `import x from \"/src/main.ts\"`;\n"
        "const built = `import ${name} from \"/src/${file}\"`;\n"
    )
    assert rewritten(source) == source


def test_a_protocol_relative_specifier_stays_on_its_own_origin() -> None:
    # `//cdn…` names another host. Prefixing it turns a CDN import into a path on the mux
    # origin, which is what the lexical rewrite did.
    source = 'import lib from "//cdn.example.com/lib.js";\n'
    assert rewritten(source) == source


def test_single_quoted_and_multiline_specifiers_are_rewritten() -> None:
    source = "import {\n  a,\n  b,\n} from '/src/pair.ts';\n"
    assert rewritten(source) == f"import {{\n  a,\n  b,\n}} from '{PREFIX}src/pair.ts';\n"


def test_a_relative_or_bare_specifier_is_untouched() -> None:
    source = 'import a from "./local.js";\nimport b from "preact";\nimport c from "../up.js";\n'
    assert rewritten(source) == source


def test_a_body_that_is_not_javascript_falls_back_to_the_lexical_rewrite() -> None:
    # Better a rewrite that can overreach than a Preview whose modules all 404. The
    # fallback is today's behaviour exactly, and it is reached only here.
    source = 'import a from "/keep.js"; this ) is ( not javascript {'
    assert _JS_SPECIFIERS.spans(source.encode("utf-8")) is None
    assert f'"{PREFIX}keep.js"' in rewritten(source)


def test_an_empty_or_specifier_free_module_is_returned_unchanged() -> None:
    assert rewritten("") == ""
    assert rewritten("export const value = 1;\n") == "export const value = 1;\n"


def test_inline_module_scripts_get_the_same_treatment_and_data_blocks_get_none() -> None:
    source = (
        b'<head><script type="module">import { hook } from "/@react-refresh";\n'
        b'const sample = \'import x from "/src/main.ts"\';</script>'
        b'<script type="application/json">{"src": "/keep/me.json"}</script>'
        b'<script type="importmap">{"imports": {"a": "/keep/a.js"}}</script>'
        b'<script type="module" src="/src/main.tsx"></script></head>'
    )
    out = rewrite_preview_html(source, PREFIX).decode("utf-8")

    assert f'from "{PREFIX}@react-refresh"' in out
    # The string beside it, in the same inline module, is the app's own data.
    assert '\'import x from "/src/main.ts"\'' in out
    assert '{"src": "/keep/me.json"}' in out
    assert '{"imports": {"a": "/keep/a.js"}}' in out
    assert f'src="{PREFIX}src/main.tsx"' in out


def test_utf8_beyond_the_ascii_range_survives_the_byte_level_rewrite() -> None:
    # The rewrite works in bytes, because tree-sitter's offsets are byte offsets. A
    # multi-byte character before a specifier would shift every span if it did not.
    source = 'const label = "café ☕";\nimport a from "/src/café.ts";\n'
    assert rewritten(source) == (
        'const label = "café ☕";\n' f'import a from "{PREFIX}src/café.ts";\n'
    )
