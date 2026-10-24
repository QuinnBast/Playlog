"""Reader for Valve's KeyValues text format (.vdf / .acf files).

Steam isn't consistent about key case (localconfig.vdf has "Software" > "Valve" > "steam"), so lookups go through
get_path(), which ignores case. Values are always strings; nested blocks are dicts.
"""

from __future__ import annotations

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VdfError(ValueError):
    pass


def _tokens(text: str):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
        elif c == "/" and text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end + 1
        elif c in "{}":
            yield c
            i += 1
        elif c == "[":
            # Platform conditionals like [$WIN32] after a value; ignored.
            end = text.find("]", i)
            if end < 0:
                raise VdfError("unterminated conditional")
            i = end + 1
        elif c == '"':
            i += 1
            out = []
            while True:
                if i >= n:
                    raise VdfError("unterminated string")
                c = text[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\" and i + 1 < n and text[i + 1] in _ESCAPES:
                    out.append(_ESCAPES[text[i + 1]])
                    i += 2
                else:
                    out.append(c)
                    i += 1
            yield ("s", "".join(out))
        else:
            start = i
            while i < n and not text[i].isspace() and text[i] not in '{}"':
                i += 1
            yield ("s", text[start:i])


def loads(text: str) -> dict:
    """Parses VDF text into nested dicts. A repeated key keeps its last value (blocks are merged)."""
    if text.startswith("﻿"):
        text = text[1:]
    root: dict = {}
    stack = [root]
    key = None
    for tok in _tokens(text):
        if tok == "{":
            if key is None:
                raise VdfError("block without a key")
            existing = stack[-1].get(key)
            child = existing if isinstance(existing, dict) else {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif tok == "}":
            if key is not None or len(stack) == 1:
                raise VdfError("unexpected }")
            stack.pop()
        elif key is None:
            key = tok[1]
        else:
            stack[-1][key] = tok[1]
            key = None
    if key is not None or len(stack) != 1:
        raise VdfError("unexpected end of file")
    return root


def load(path) -> dict:
    with open(path, encoding="utf-8", errors="replace") as f:
        return loads(f.read())


def get_path(node, *keys):
    """Walks nested dicts by key, ignoring case. Returns None if any key is missing."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        if key in node:
            node = node[key]
            continue
        lower = key.lower()
        node = next((v for k, v in node.items() if k.lower() == lower), None)
        if node is None:
            return None
    return node
