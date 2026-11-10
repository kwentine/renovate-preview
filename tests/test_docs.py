"""The files under docs/ are tangled from the org pages that show them.

After editing a block, re-tangle: C-c C-v t in Emacs, or
    emacs --batch -l org --eval '(org-babel-tangle-file "docs/configuration.org")'
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).parent.parent / "docs"
BLOCK = re.compile(
    r"^#\+begin_src \S+ [^\n]*:tangle (\S+)[^\n]*\n(.*?)^#\+end_src", re.M | re.S | re.I
)
SHEBANG = re.compile(r':shebang "([^"]+)"')


def blocks():
    """Yield (page, the tangled file's path, what the block says it holds)."""
    for page in sorted(DOCS.glob("*.org")):
        for match in BLOCK.finditer(page.read_text()):
            path, body = match.groups()
            shebang = SHEBANG.search(match.group(0).splitlines()[0])
            expected = (shebang.group(1) + "\n" if shebang else "") + body
            yield pytest.param(page, path, expected, id=f"{page.name}:{path}")


@pytest.mark.parametrize(("page", "path", "expected"), list(blocks()))
def test_tangled_files_match_their_blocks(page, path, expected):
    assert (page.parent / path).read_text() == expected
