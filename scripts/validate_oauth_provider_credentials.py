"""Runner-only OAuth prerequisite checks; outputs contain decisions, never values."""
from __future__ import annotations

import ast
import os
import re
import subprocess
from pathlib import Path

ID = "TINYASSETS_OAUTH_GOOGLE_CLIENT_ID"
SECRET = "TINYASSETS_OAUTH_GOOGLE_CLIENT_SECRET"
INSTALL = "TINYASSETS_OAUTH_CREDENTIALS_INSTALL"


def target_protects_children(revision: str) -> bool:
    def read(path: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "show", f"{revision}:{path}"], capture_output=True, text=True, check=False,
        )

    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        return False
    if read("tinyassets/connection_oauth/providers.json").returncode:
        return False
    source = read("tinyassets/platform_secrets.py")
    if source.returncode:
        return False
    try:
        tree = ast.parse(source.stdout)
    except SyntaxError:
        return False
    # Check the actual returned comprehension, not a comment or unused constant.
    # Unknown future implementations conservatively leave OAuth disabled.
    expected = ast.dump(ast.parse('not name.startswith("TINYASSETS_OAUTH_")', mode="eval").body)
    for function in tree.body:
        if isinstance(function, ast.FunctionDef) and function.name == "child_env":
            for statement in function.body:
                if isinstance(statement, ast.Return) and isinstance(statement.value, ast.DictComp):
                    for generator in statement.value.generators:
                        for condition in generator.ifs:
                            terms = condition.values if (
                                isinstance(condition, ast.BoolOp)
                                and isinstance(condition.op, ast.And)
                            ) else [condition]
                            if any(ast.dump(term) == expected for term in terms):
                                return True
    return False


def main() -> int:
    if os.environ.get(INSTALL, "").lower() != "true":
        print("::notice::OAuth credential installation disabled; removing any retained pair")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write("action=remove\n")
        return 0
    values = [os.environ.get(key, "") for key in (ID, SECRET)]
    action = "skip"
    if any(values) and not all(values):
        print(f"::warning::configure both repository variable {ID} and repository secret {SECRET}; "
              "skipping OAuth credentials")
    elif all(values):
        for key, value in zip((ID, SECRET), values, strict=True):
            if "\n" in value or "\r" in value:
                print(f"::error::{key} must be single-line")
                return 1
            # Raw dotenv is shared by Compose and systemd. Permit only portable
            # unquoted credential characters (including '='), no interpolation.
            if not re.fullmatch(r"[A-Za-z0-9._~:/+=,@%-]+", value):
                print(f"::error::{key} must use portable unquoted credential characters")
                return 1
        action = "install"
    if not target_protects_children(os.environ.get("TARGET_REVISION", "")):
        print("::warning::target lacks OAuth child filtering; "
              "skipping install and removing OAuth keys")
        action = "remove"
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"action={action}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
