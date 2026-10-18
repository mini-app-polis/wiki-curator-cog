"""The package imports where there is no git binary: the Lambda runtime."""

from __future__ import annotations

import os
import subprocess
import sys


def test_every_module_imports_with_no_git_on_the_path(tmp_path) -> None:
    # An empty PATH: no git anywhere. A fresh interpreter, so nothing the
    # test session already imported hides the failure.
    code = (
        "import importlib, pkgutil, wiki_curator_cog as p\n"
        "for m in pkgutil.iter_modules(p.__path__):\n"
        "    importlib.import_module(f'wiki_curator_cog.{m.name}')\n"
        "print('ok')\n"
    )
    env = dict(os.environ)
    env["PATH"] = str(tmp_path)
    out = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"
