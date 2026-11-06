"""The files under docs/ are tangled from the org pages that show them.

After editing a block, re-tangle: C-c C-v t in Emacs, or
    emacs --batch -l org --eval '(org-babel-tangle-file "docs/configuration.org")'
"""

import re
import unittest
from pathlib import Path

DOCS = Path(__file__).parent.parent / "docs"
BLOCK = re.compile(
    r"^#\+begin_src \S+ [^\n]*:tangle (\S+)[^\n]*\n(.*?)^#\+end_src", re.M | re.S | re.I
)
SHEBANG = re.compile(r':shebang "([^"]+)"')


class TangleTests(unittest.TestCase):
    def test_tangled_files_match_their_blocks(self):
        for page in DOCS.glob("*.org"):
            text = page.read_text()
            for match in BLOCK.finditer(text):
                path, body = match.groups()
                shebang = SHEBANG.search(match.group(0).splitlines()[0])
                expected = (shebang.group(1) + "\n" if shebang else "") + body
                with self.subTest(file=path):
                    self.assertEqual((page.parent / path).read_text(), expected)
