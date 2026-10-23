#!/usr/bin/env python3
"""Check app/: the inline script parses, every t("key") exists in every language file, and no
language file carries keys the app no longer uses. Run after every edit of app/ (AGENTS.md).

Exit 0 = fine. Needs `node` for the syntax check; without it that part is skipped with a note.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
APP = os.path.join(ROOT, "app")


def main():
    html = open(os.path.join(APP, "index.html"), encoding="utf-8").read()
    problems = []

    scripts = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    if len(scripts) != 1:
        problems.append("expected exactly one inline <script>, found %d" % len(scripts))
    elif shutil.which("node"):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
            f.write(scripts[0])
        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        os.unlink(f.name)
        if r.returncode:
            problems.append("script does not parse:\n" + r.stderr.strip())
    else:
        print("note: node not found, syntax not checked")

    # Keys look like "area.word"; that also skips the t("key") in comments.
    used = set(re.findall(r'\bt\("([a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)"', html))
    # Keys chosen by a condition, t(x ? "a.b" : "c.d"): every quoted key in such a call.
    for call in re.findall(r'\bt\(([^()]*\?[^()]*)\)', html):
        used |= set(re.findall(r'"([a-zA-Z0-9_]+\.[a-zA-Z0-9_.]+)"', call))
    langs = sorted(f for f in os.listdir(os.path.join(APP, "i18n")) if f.endswith(".json"))
    for name in langs:
        texts = json.load(open(os.path.join(APP, "i18n", name), encoding="utf-8"))
        for key in sorted(used - texts.keys()):
            problems.append("%s: missing key %s" % (name, key))
        for key in sorted(texts.keys() - used):
            problems.append("%s: unused key %s" % (name, key))

    # Shadowing `t` breaks every t("…") call in the same function (AGENTS.md rule 3).
    for m in re.finditer(r"\b(?:const|let|var)\s+t\b\s*=", html):
        problems.append("`t` is shadowed at offset %d" % m.start())

    for p in problems:
        print("FAIL", p)
    print("%d keys, %d languages, %s" % (len(used), len(langs), "ok" if not problems else
                                          "%d problems" % len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
