"""
Lists every English text the window can show, for translators.

    python tools/extract_strings.py            write cncbridge/locales/template.json
    python tools/extract_strings.py --check    exit 1 when the committed template is out of date

A text is found where the code calls tr("...") or marks it with N_("...") (the window), and in the Help topics and
shortcut descriptions (cncbridge/help.py). To add a language, copy template.json to <code>.json (for example
de.json), set "language" to the language's own name and fill in "strings": leave a text empty to keep English.
Keep every {placeholder} of the English text in the translation.
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "cncbridge"
TEMPLATE = PACKAGE / "locales" / "template.json"


def worth_translating(text):
    """True when the text has words: not just placeholders, spaces and symbols."""
    return bool(re.search(r"[A-Za-z]{2,}", re.sub(r"\{[^}]*\}", "", text)))


def from_calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "N_") \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            yield node.lineno, node.args[0].value


def from_help(tree):
    """The Help topics (title, text) and the shortcut descriptions are plain data in help.py."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "TOPICS":
                for _key, title, body in ast.literal_eval(node.value):
                    yield node.lineno, title
                    if body:
                        yield node.lineno, body
            elif name == "SHORTCUTS":
                for _keys, what in ast.literal_eval(node.value):
                    yield node.lineno, what


def from_extension_help(tree):
    """Help modules may expose one generic HELP_TOPIC tuple for their optional UI extension."""
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "HELP_TOPIC" for target in node.targets):
            continue
        try:
            _key, title, body = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        yield node.lineno, title
        if body:
            yield node.lineno, body


def extract(package=PACKAGE):
    """{english text: ""} in a stable order (by file, then position)."""
    found = {}
    for path in sorted(Path(package).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        items = list(from_calls(tree)) + list(from_extension_help(tree))
        if path.name == "help.py" and path.parent == Path(package):
            items += list(from_help(tree))
        for _line, text in sorted(items, key=lambda i: i[0]):
            if worth_translating(text):
                found.setdefault(text.strip(), "")  # padding is kept by tr() and need not be translated
    return found


def render(strings):
    return json.dumps({"language": "", "strings": strings}, indent=2, ensure_ascii=False) + "\n"


def main(argv):
    text = render(extract())
    if "--check" in argv:
        current = TEMPLATE.read_text(encoding="utf-8") if TEMPLATE.exists() else ""
        if current != text:
            print("cncbridge/locales/template.json is out of date: run python tools/extract_strings.py")
            return 1
        print("template.json is up to date")
        return 0
    TEMPLATE.parent.mkdir(parents=True, exist_ok=True)
    TEMPLATE.write_text(text, encoding="utf-8", newline="\n")
    print(f"{len(json.loads(text)['strings'])} texts written to {TEMPLATE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
