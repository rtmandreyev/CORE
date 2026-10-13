"""Minimal-diff editing of JSON-with-comments files (VS Code settings.json).

`load()` reads JSONC (// and /* */ comments, trailing commas) into Python.
`set_path()` / `delete_path()` change one member, one or two levels deep, by
splicing text: every other byte, comment and blank line in the file is kept.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field


class JsoncError(ValueError):
    pass


@dataclass
class Member:
    key: str
    start: int  # first character of the key string
    value_start: int
    value_end: int
    node: "Node | None" = None


@dataclass
class Node:
    kind: str  # object, array, scalar
    start: int
    end: int  # one past the closing character
    members: list[Member] = field(default_factory=list)


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def skip(self) -> None:
        text = self.text
        while self.pos < len(text):
            char = text[self.pos]
            if char in " \t\r\n﻿":
                self.pos += 1
            elif text.startswith("//", self.pos):
                newline = text.find("\n", self.pos)
                self.pos = len(text) if newline < 0 else newline + 1
            elif text.startswith("/*", self.pos):
                close = text.find("*/", self.pos + 2)
                if close < 0:
                    raise JsoncError("unterminated comment")
                self.pos = close + 2
            else:
                return

    def value(self) -> Node:
        self.skip()
        if self.pos >= len(self.text):
            raise JsoncError("unexpected end of document")
        char = self.text[self.pos]
        if char == "{":
            return self.obj()
        if char == "[":
            return self.arr()
        if char == '"':
            start = self.pos
            self.string()
            return Node("scalar", start, self.pos)
        match = re.compile(r"-?\d+(\.\d+)?([eE][+-]?\d+)?|true|false|null").match(self.text, self.pos)
        if not match:
            raise JsoncError(f"unexpected character {char!r} at offset {self.pos}")
        start, self.pos = self.pos, match.end()
        return Node("scalar", start, self.pos)

    def string(self) -> str:
        start = self.pos
        self.pos += 1
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 2
                continue
            if char == '"':
                self.pos += 1
                return json.loads(self.text[start : self.pos])
            self.pos += 1
        raise JsoncError("unterminated string")

    def obj(self) -> Node:
        node = Node("object", self.pos, -1)
        self.pos += 1
        while True:
            self.skip()
            if self.text[self.pos : self.pos + 1] == "}":
                self.pos += 1
                node.end = self.pos
                return node
            if self.text[self.pos : self.pos + 1] != '"':
                raise JsoncError(f"expected a key at offset {self.pos}")
            key_start = self.pos
            key = self.string()
            self.skip()
            if self.text[self.pos : self.pos + 1] != ":":
                raise JsoncError(f"expected ':' at offset {self.pos}")
            self.pos += 1
            value = self.value()
            node.members.append(Member(key, key_start, value.start, value.end, value))
            self.skip()
            if self.text[self.pos : self.pos + 1] == ",":
                self.pos += 1

    def arr(self) -> Node:
        node = Node("array", self.pos, -1)
        self.pos += 1
        while True:
            self.skip()
            if self.text[self.pos : self.pos + 1] == "]":
                self.pos += 1
                node.end = self.pos
                return node
            self.value()
            self.skip()
            if self.text[self.pos : self.pos + 1] == ",":
                self.pos += 1


def _strip(text: str) -> str:
    """JSONC -> JSON text (comments and trailing commas removed)."""

    out: list[str] = []
    index = 0
    in_string = False

    while index < len(text):
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\":
                out.append(text[index + 1 : index + 2])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline
        elif text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = len(text) if close < 0 else close + 2
        else:
            out.append(char)
            index += 1

    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def load(text: str | None) -> dict:
    if text is None or not text.strip():
        return {}
    try:
        value = json.loads(_strip(text))
    except json.JSONDecodeError as exc:
        raise JsoncError(f"not valid JSON with comments: {exc}") from None
    if not isinstance(value, dict):
        raise JsoncError("the top level is not an object")
    return value


def parse(text: str) -> Node:
    parser = _Parser(text)
    node = parser.value()
    if node.kind != "object":
        raise JsoncError("the top level is not an object")
    parser.skip()
    if parser.pos != len(text):
        raise JsoncError(f"unexpected content after the top-level object at offset {parser.pos}")
    return node


def _line_indent(text: str, offset: int) -> str:
    line_start = text.rfind("\n", 0, offset) + 1
    return re.match(r"[ \t]*", text[line_start:]).group(0)


def _unit(text: str, node: Node) -> str:
    if node.members:
        member_indent = _line_indent(text, node.members[0].start)
        outer = _line_indent(text, node.start)
        if member_indent.startswith(outer) and len(member_indent) > len(outer):
            return member_indent[len(outer) :]
    return "    "


def _dump(value: object, indent: str, unit: str) -> str:
    text = json.dumps(value, indent=unit, ensure_ascii=False)
    return text.replace("\n", "\n" + indent)


def _find(node: Node, key: str) -> Member | None:
    found = [member for member in node.members if member.key == key]
    return found[-1] if found else None  # VS Code honours the last duplicate


def get_path(text: str | None, path: list[str]) -> object:
    value: object = load(text)
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return _MISSING
        value = value[key]
    return value


_MISSING = object()


def missing(value: object) -> bool:
    return value is _MISSING


def set_path(text: str | None, path: list[str], value: object) -> str:
    """Set `path` (1 or 2 keys) to `value`, creating the parent object if needed."""

    if text is None or not text.strip():
        text = "{\n}\n"

    root = parse(text)
    unit = _unit(text, root)
    node, depth = root, 0

    while depth < len(path) - 1:
        member = _find(node, path[depth])
        if member is None or member.node is None or member.node.kind != "object":
            nested: object = value
            for key in reversed(path[depth + 1 :]):
                nested = {key: nested}
            return _put(text, node, path[depth], nested, unit, existing=member)
        node = member.node
        depth += 1

    return _put(text, node, path[-1], value, unit, existing=_find(node, path[-1]))


def _put(text: str, node: Node, key: str, value: object, unit: str, *, existing: Member | None) -> str:
    if existing is not None:
        indent = _line_indent(text, existing.start)
        return text[: existing.value_start] + _dump(value, indent, unit) + text[existing.value_end :]

    outer = _line_indent(text, node.start)
    indent = outer + unit
    entry = f"{json.dumps(key)}: {_dump(value, indent, unit)}"

    if node.members:
        last = node.members[-1]
        return text[: last.value_end] + ",\n" + indent + entry + text[last.value_end :]

    inner = text[node.start + 1 : node.end - 1]
    if inner.strip():  # only comments inside: keep them, add after
        return text[: node.end - 1].rstrip() + "\n" + indent + entry + "\n" + outer + text[node.end - 1 :]
    return text[: node.start + 1] + "\n" + indent + entry + "\n" + outer + text[node.end - 1 :]


def delete_path(text: str | None, path: list[str]) -> str | None:
    """Remove `path`; returns the text unchanged when it is absent."""

    if text is None or not text.strip():
        return text

    node = parse(text)
    for key in path[:-1]:
        member = _find(node, key)
        if member is None or member.node is None or member.node.kind != "object":
            return text
        node = member.node

    member = _find(node, path[-1])
    if member is None:
        return text

    index = node.members.index(member)
    start = text.rfind("\n", 0, member.start) + 1
    if text[start : member.start].strip():
        start = member.start
    end = member.value_end
    rest = re.match(r"[ \t]*,?", text[end:]).group(0)

    if rest.endswith(","):
        end += len(rest)
    elif index > 0:
        previous = node.members[index - 1]
        comma = text.find(",", previous.value_end, member.start)
        if comma >= 0:
            start = comma
            end = member.value_end

    newline = re.match(r"[ \t]*\n", text[end:])
    if newline and start == text.rfind("\n", 0, member.start) + 1:
        end += newline.end()

    return text[:start] + text[end:]
