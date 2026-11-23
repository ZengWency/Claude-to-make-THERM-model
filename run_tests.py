"""Run every skill's unittests: python run_tests.py

Each skill runs in its own Python process, because every skill names its test
package `tests`. Fails if any skill fails or has no tests. Windows or Linux,
standard library only.
"""
import os
import re
import subprocess
import sys

SKILLS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


def main():
    rows, failed = [], False
    for name in sorted(os.listdir(SKILLS)):
        skill = os.path.join(SKILLS, name)
        if not os.path.isfile(os.path.join(skill, "SKILL.md")):
            continue
        if not os.path.isdir(os.path.join(skill, "tests")):
            rows.append((name, 0, "NO TESTS"))
            failed = True
            continue
        proc = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                              cwd=skill, capture_output=True, text=True)
        ran = re.search(r"^Ran (\d+) tests?", proc.stderr, re.M)
        count = int(ran.group(1)) if ran else 0
        ok = proc.returncode == 0 and count > 0
        rows.append((name, count, "ok" if ok else "FAILED"))
        if not ok:
            failed = True
            print("---- %s ----\n%s" % (name, proc.stderr), file=sys.stderr)
    width = max(len(r[0]) for r in rows)
    for name, count, status in rows:
        print("%-*s  %4d tests  %s" % (width, name, count, status))
    print("%-*s  %4d tests  %s" % (width, "TOTAL", sum(r[1] for r in rows), "FAILED" if failed else "ok"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
