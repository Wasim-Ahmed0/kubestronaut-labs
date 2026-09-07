"""Zero-dependency YAML subset parser.

Supports exactly the subset used by the lab catalogue:
  mappings, sequences, nested blocks, block scalars (| |- |+ > >-),
  flow sequences ([a, b]), quoted scalars, comments, and the usual
  bool/int/float/null literals.

If PyYAML happens to be installed we defer to it, since it is strictly
better.  The fallback exists so the platform runs on a bare Python 3.
"""
from __future__ import annotations

import re

try:  # pragma: no cover - environment dependent
    import yaml as _pyyaml
except Exception:  # pragma: no cover
    _pyyaml = None


class YamlError(ValueError):
    pass


_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _scalar(tok: str):
    t = tok.strip()
    if t == "" or t == "~" or t == "null":
        return None
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "'\"":
        body = t[1:-1]
        if t[0] == '"':
            body = body.replace('\\n', '\n').replace('\\t', '\t')
            body = body.replace('\\"', '"').replace('\\\\', '\\')
        else:
            body = body.replace("''", "'")
        return body
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if _INT_RE.match(t):
        return int(t)
    if _FLOAT_RE.match(t) and not _INT_RE.match(t):
        try:
            return float(t)
        except ValueError:
            pass
    return t


def _flow_seq(tok: str):
    inner = tok.strip()[1:-1].strip()
    if not inner:
        return []
    parts, buf, quote, depth = [], "", None, 0
    for ch in inner:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    return [_scalar(p) for p in parts if p.strip() != ""]


def _strip_comment(line: str) -> str:
    """Remove a trailing ``#`` comment that sits outside of quotes."""
    out, quote = "", None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            out += ch
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
            out += ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        else:
            out += ch
        i += 1
    return out.rstrip()


class _Line:
    __slots__ = ("indent", "text", "no")

    def __init__(self, indent: int, text: str, no: int):
        self.indent, self.text, self.no = indent, text, no


def _tokenize(src: str):
    lines = []
    for no, raw in enumerate(src.splitlines(), 1):
        if raw.strip().startswith("#") or not raw.strip():
            lines.append(None)  # placeholder keeps block scalars honest
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        lines.append(_Line(indent, raw, no))
    return lines


def _block_scalar(raw_lines, idx, parent_indent, style):
    """Collect a block scalar body starting at ``idx``."""
    chomp = "clip"
    if style.endswith("-"):
        chomp = "strip"
    elif style.endswith("+"):
        chomp = "keep"
    folded = style[0] == ">"

    body, base = [], None
    i = idx
    while i < len(raw_lines):
        raw = raw_lines[i]
        if raw.strip() == "":
            body.append("")
            i += 1
            continue
        ind = len(raw) - len(raw.lstrip(" "))
        if ind <= parent_indent:
            break
        if base is None:
            base = ind
        body.append(raw[base:] if len(raw) >= base else raw.lstrip(" "))
        i += 1

    while body and body[-1] == "":
        body.pop()

    if folded:
        folded_out, para = [], []
        for ln in body:
            if ln.strip() == "":
                folded_out.append(" ".join(para))
                para = []
                folded_out.append("")
            elif ln.startswith(" "):
                if para:
                    folded_out.append(" ".join(para))
                    para = []
                folded_out.append(ln)
            else:
                para.append(ln.strip())
        if para:
            folded_out.append(" ".join(para))
        text = "\n".join(folded_out)
    else:
        text = "\n".join(body)

    if chomp == "strip":
        text = text.rstrip("\n")
    elif text:
        text = text.rstrip("\n") + "\n"
    return text, i


class _Parser:
    def __init__(self, src: str):
        self.raw = src.splitlines()
        self.lines = _tokenize(src)
        self.i = 0

    def _peek(self):
        while self.i < len(self.lines) and self.lines[self.i] is None:
            self.i += 1
        if self.i >= len(self.lines):
            return None
        return self.lines[self.i]

    def parse(self):
        ln = self._peek()
        if ln is None:
            return None
        return self._node(ln.indent)

    def _node(self, indent):
        ln = self._peek()
        if ln is None or ln.indent < indent:
            return None
        if ln.text.lstrip().startswith("- "):
            return self._seq(ln.indent)
        return self._map(ln.indent)

    def _seq(self, indent):
        items = []
        while True:
            ln = self._peek()
            if ln is None or ln.indent < indent:
                break
            stripped = ln.text.lstrip()
            if ln.indent > indent:
                raise YamlError(f"line {ln.no}: unexpected indent in sequence")
            if not (stripped.startswith("- ") or stripped == "-"):
                break
            rest = _strip_comment(stripped[1:].lstrip() if stripped != "-" else "")
            if rest == "":
                self.i += 1
                nxt = self._peek()
                items.append(self._node(nxt.indent) if nxt and nxt.indent > indent else None)
                continue
            # inline content on the dash line
            child_indent = ln.indent + (len(stripped) - len(stripped[1:].lstrip()) )
            if self._is_key(rest):
                # rewrite the line so the map parser sees it at child indent
                pad = ln.indent + 2
                self.lines[self.i] = _Line(pad, " " * pad + rest, ln.no)
                items.append(self._map(pad, single_dash=True))
            else:
                self.i += 1
                items.append(self._inline_value(rest, ln.indent))
        return items

    @staticmethod
    def _is_key(text: str) -> bool:
        m = re.match(r"^[A-Za-z0-9_.\-\"']+\s*:(\s|$)", text)
        return bool(m)

    def _map(self, indent, single_dash=False):
        out = {}
        while True:
            ln = self._peek()
            if ln is None or ln.indent < indent:
                break
            if ln.indent > indent:
                raise YamlError(f"line {ln.no}: unexpected indent {ln.indent} (want {indent})")
            stripped = ln.text.lstrip()
            if stripped.startswith("- "):
                break
            if not self._is_key(stripped):
                raise YamlError(f"line {ln.no}: expected 'key:' but got {stripped[:40]!r}")
            key, _, rest = stripped.partition(":")
            key = _scalar(key.strip())
            rest = _strip_comment(rest.strip())
            if rest == "":
                self.i += 1
                nxt = self._peek()
                if nxt is not None and nxt.indent > indent:
                    out[key] = self._node(nxt.indent)
                elif nxt is not None and nxt.indent == indent and nxt.text.lstrip().startswith("- "):
                    out[key] = self._seq(indent)
                else:
                    out[key] = None
            elif rest[0] in "|>":
                style = rest
                start = self.i + 1
                text, end = _block_scalar(self.raw, start, indent, style)
                out[key] = text
                self.i = end
                # keep tokenized view in sync
                for j in range(start, min(end, len(self.lines))):
                    self.lines[j] = None
            else:
                self.i += 1
                out[key] = self._inline_value(rest, indent)
            if single_dash:
                # continue consuming sibling keys of this list item
                pass
        return out

    def _inline_value(self, rest, indent):
        if rest.startswith("["):
            return _flow_seq(rest)
        if rest.startswith("{"):
            raise YamlError("flow mappings are not supported")
        return _scalar(rest)


def safe_load(src: str):
    if _pyyaml is not None:
        return _pyyaml.safe_load(src)
    return _Parser(src).parse()


def load_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        return safe_load(fh.read())
