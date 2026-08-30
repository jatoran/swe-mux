"""The pinned desktop closure, generated from `uv.lock`. Do not edit by hand.

Regenerate with `uv run python packaging/generate_desktop_pins.py --write` after
any change to the `desktop` extra or its transitive resolution.
`tests/test_desktop_wheels.py` fails when this file and `uv.lock` disagree,
because a stale table means a first-use download of a closure this repository
never audited.

The table is every wheel `uv.lock` records for every distribution reachable only
through the `desktop` extra. Which of them this machine wants is a runtime
question (`wheels_for_this_interpreter`), answered against the running
interpreter's own `packaging.tags`. The consumer is
`swe_mux.desktop_runtime`; `packaging/generate_desktop_pins.py` documents the
derivation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DesktopWheel:
    """One pinned wheel: what it is, where it is, and what it must hash to."""

    distribution: str
    version: str
    filename: str
    url: str
    sha256: str
    size: int


#: SHA-256 over (filename, sha256, size) of every pin, in table order; the stable
#: identity for "this exact desktop closure".
CLOSURE_DIGEST = "545a678e39699e3c009f524bc41bcf23e8d015f224112e1a4cb84cc4bc77ee1f"

#: The distributions acquired at first use, in dependency-name order.
DISTRIBUTIONS: tuple[str, ...] = (
    "bottle",
    "clr-loader",
    "pystray",
    "pythonnet",
    "pywebview",
    "six",
)

WHEELS: tuple[DesktopWheel, ...] = (
    DesktopWheel("bottle", "0.13.4", "bottle-0.13.4-py2.py3-none-any.whl", "https://files.pythonhosted.org/packages/83/f6/b55ec74cfe68c6584163faa311503c20b0da4c09883a41e8e00d6726c954/bottle-0.13.4-py2.py3-none-any.whl", "045684fbd2764eac9cdeb824861d1551d113e8b683d8d26e296898d3dd99a12e", 103807),
    DesktopWheel("clr-loader", "0.3.1", "clr_loader-0.3.1-py3-none-any.whl", "https://files.pythonhosted.org/packages/5e/da/ec1a6e36624000b6df0dd61183c42342ee5814c073315e802cadaad04d2f/clr_loader-0.3.1-py3-none-any.whl", "cbad189de20d202a7d621956b0fc38049e13c9bf7ca2923441eff725cd121aa1", 55730),
    DesktopWheel("pystray", "0.19.5", "pystray-0.19.5-py2.py3-none-any.whl", "https://files.pythonhosted.org/packages/5c/64/927a4b9024196a4799eba0180e0ca31568426f258a4a5c90f87a97f51d28/pystray-0.19.5-py2.py3-none-any.whl", "a0c2229d02cf87207297c22d86ffc57c86c227517b038c0d3c59df79295ac617", 49068),
    DesktopWheel("pythonnet", "3.1.0", "pythonnet-3.1.0-cp310.cp311.cp312.cp313.cp314-none-any.whl", "https://files.pythonhosted.org/packages/ac/4b/52414f442624d2589f5374a48c08d5ae94f24bea67fc13a20a752884e5b7/pythonnet-3.1.0-cp310.cp311.cp312.cp313.cp314-none-any.whl", "698dd88edc198819ad63b624a6ebe76208c7b46e4fe13626f65e484f0358d6ba", 217578),
    DesktopWheel("pythonnet", "3.1.0", "pythonnet-3.1.0-cp310.cp311.cp312.cp313.cp314-none-win32.win_amd64.whl", "https://files.pythonhosted.org/packages/db/67/031124fdcb937c266a3265118525bbf6dc13b8c79786d6a7290aecb6e7bb/pythonnet-3.1.0-cp310.cp311.cp312.cp313.cp314-none-win32.win_amd64.whl", "7bdd4de03df3547a48122a3989265c8b31d5be0d19dadffa009eec7df8085e0b", 1644898),
    DesktopWheel("pywebview", "6.2.1", "pywebview-6.2.1-py3-none-any.whl", "https://files.pythonhosted.org/packages/3d/25/9491695c22c4842c5b3903b4dc172e0eecf67a27c0af34a71512c9b76a0a/pywebview-6.2.1-py3-none-any.whl", "9d07275f53894ab4d5e2e0e996227193e7187dec276d9b624dccbce029216b46", 525463),
    DesktopWheel("six", "1.17.0", "six-1.17.0-py2.py3-none-any.whl", "https://files.pythonhosted.org/packages/b7/ce/149a00dd41f10bc29e5921b496af8b574d8413afcd5e30dfa0ed46c2cc5e/six-1.17.0-py2.py3-none-any.whl", "4721f391ed90541fddacab5acf947aa0d3dc7d27b2e1e8eda2be8970586c3274", 11050),
)

#: Distributions that publish no wheel at all, pinned as sdists and extracted -
#: never built - by `wheel_closure._extract_sdist`. `proxy-tools` is the reason
#: this exists: pywebview imports it unconditionally and PyPI has only its
#: sdist (2,978 bytes of pure Python).
SDISTS: tuple[DesktopWheel, ...] = (
    DesktopWheel("proxy-tools", "0.1.0", "proxy_tools-0.1.0.tar.gz", "https://files.pythonhosted.org/packages/f2/cf/77d3e19b7fabd03895caca7857ef51e4c409e0ca6b37ee6e9f7daa50b642/proxy_tools-0.1.0.tar.gz", "ccb3751f529c047e2d8a58440d86b205303cf0fe8146f784d1cbcd94f0a28010", 2978),
)


def wheels_for_this_interpreter(
    wheels: tuple[DesktopWheel, ...] = WHEELS,
) -> tuple[DesktopWheel, ...]:
    """The one wheel per distribution this interpreter can load, best tag first.

    Same selection as `voice_wheels.wheels_for_this_interpreter`, for the same
    reasons; raises `LookupError` naming the distributions with no loadable
    wheel rather than performing a partial install.
    """
    from packaging.tags import sys_tags
    from packaging.utils import parse_wheel_filename

    order = {tag: index for index, tag in enumerate(sys_tags())}
    best: dict[str, tuple[int, DesktopWheel]] = {}
    for wheel in wheels:
        try:
            _, _, _, tags = parse_wheel_filename(wheel.filename)
        except Exception:  # noqa: BLE001 - an unparseable filename is simply not a candidate
            continue
        ranks = [order[tag] for tag in tags if tag in order]
        if not ranks:
            continue
        rank = min(ranks)
        current = best.get(wheel.distribution)
        if current is None or rank < current[0]:
            best[wheel.distribution] = (rank, wheel)

    missing = sorted(set(DISTRIBUTIONS) - set(best))
    if missing:
        raise LookupError(
            "the pinned desktop closure has no wheel this interpreter can load for: "
            + ", ".join(missing)
        )
    return tuple(wheel for _, wheel in sorted(best.values(), key=lambda item: item[1].filename))
