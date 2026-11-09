"""Parser for review "map" files (openHAB-sitemap-like DSL).

Grammar (whitespace/newlines are insignificant, '#' starts a comment):

    node   := Type attr* [ '{' node* '}' ]
    attr   := key '=' value | flag
    value  := "string" | \"\"\"multi-line string\"\"\" | number | word
    Type   := Capitalised word  (Sitemap, Frame, Text, Textarea, Markdown,
                                 Choice, Option, Checkbox, Info)
    key    := lowercase word    (flag = lowercase word not followed by '=')
"""
import re

TYPES = {"Sitemap", "Frame", "Text", "Textarea", "Markdown", "Choice",
         "Option", "Checkbox", "Info"}
FIELD_TYPES = {"Text", "Textarea", "Markdown", "Choice", "Checkbox"}
CONTAINERS = {"Sitemap", "Frame", "Choice"}


class MapError(ValueError):
    pass


def _dedent(s):
    lines = s.strip("\n").split("\n")
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(l[cut:] if l.strip() else "" for l in lines).strip()


def tokenize(src):
    toks, i, n, line = [], 0, len(src), 1
    while i < n:
        c = src[i]
        if c == "\n":
            line += 1
            i += 1
        elif c.isspace():
            i += 1
        elif c == "#":
            while i < n and src[i] != "\n":
                i += 1
        elif c in "{}=":
            toks.append((c, c, line))
            i += 1
        elif src.startswith('"""', i):
            j = src.find('"""', i + 3)
            if j < 0:
                raise MapError(f"line {line}: unterminated \"\"\" string")
            body = src[i + 3:j]
            toks.append(("str", _dedent(body), line))
            line += body.count("\n")
            i = j + 3
        elif c == '"':
            j, buf = i + 1, []
            while j < n and src[j] != '"':
                if src[j] == "\\" and j + 1 < n:
                    buf.append({"n": "\n", '"': '"', "\\": "\\"}.get(src[j + 1], src[j + 1]))
                    j += 2
                else:
                    if src[j] == "\n":
                        raise MapError(f"line {line}: unterminated string (use \"\"\" for multi-line)")
                    buf.append(src[j])
                    j += 1
            if j >= n:
                raise MapError(f"line {line}: unterminated string")
            toks.append(("str", "".join(buf), line))
            i = j + 1
        else:
            m = re.match(r"[^\s{}=\"#]+", src[i:])
            toks.append(("word", m.group(0), line))
            i += len(m.group(0))
    return toks


def parse(src):
    toks = tokenize(src)
    pos = 0

    def peek(k=0):
        return toks[pos + k] if pos + k < len(toks) else (None, None, None)

    def parse_node():
        nonlocal pos
        kind, val, line = peek()
        if kind != "word" or val not in TYPES:
            raise MapError(f"line {line}: expected a widget type ({', '.join(sorted(TYPES))}), got {val!r}")
        pos += 1
        node = {"type": val, "attrs": {}, "children": []}
        while True:
            kind, val, line = peek()
            if kind == "word" and val not in TYPES:
                if peek(1)[0] == "=":
                    key = val
                    pos += 2
                    vk, vv, vl = peek()
                    if vk not in ("str", "word"):
                        raise MapError(f"line {vl}: missing value for '{key}'")
                    pos += 1
                    node["attrs"][key] = _coerce(vv, vk)
                else:
                    node["attrs"][val] = True
                    pos += 1
            else:
                break
        if peek()[0] == "{":
            pos += 1
            while peek()[0] != "}":
                if peek()[0] is None:
                    raise MapError("unexpected end of file: missing '}'")
                node["children"].append(parse_node())
            pos += 1
        return node

    root = parse_node()
    if root["type"] != "Sitemap":
        raise MapError("the map must start with a Sitemap")
    if pos != len(toks):
        raise MapError(f"line {peek()[2]}: unexpected content after the Sitemap")
    _validate(root, set())
    return root


def _coerce(v, kind):
    if kind == "str":
        return v
    if v in ("true", "false"):
        return v == "true"
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


def _validate(node, ids):
    t = node["type"]
    a = node["attrs"]
    if t in FIELD_TYPES:
        if "id" not in a or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(a["id"])):
            raise MapError(f"{t} '{a.get('label', '?')}' needs a valid id=")
        if a["id"] in ids:
            raise MapError(f"duplicate id '{a['id']}'")
        ids.add(a["id"])
    if t == "Choice" and not any(c["type"] == "Option" for c in node["children"]):
        raise MapError(f"Choice '{a.get('id')}' has no Option children")
    if node["children"] and t not in CONTAINERS:
        raise MapError(f"{t} cannot contain children")
    for c in node["children"]:
        if t == "Choice" and c["type"] != "Option":
            raise MapError("Choice can only contain Option")
        if c["type"] == "Option" and t != "Choice":
            raise MapError("Option is only valid inside Choice")
        _validate(c, ids)
