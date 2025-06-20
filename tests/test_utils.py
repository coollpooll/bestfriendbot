import os
import ast
import pathlib

# Set environment variables required by main module to avoid import errors
os.environ.setdefault("BOT_TOKEN", "dummy")
os.environ.setdefault("OPENAI_API_KEY", "dummy")
os.environ.setdefault("ASSISTANT_ID", "dummy")
os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost/db")

os.environ.setdefault("OWNER_CHAT_ID", "0")


def load_should_send_as_file():
    path = pathlib.Path(__file__).resolve().parents[1] / "main.py"
    src = path.read_text()
    mod = ast.parse(src)
    func = next(n for n in mod.body if isinstance(n, ast.FunctionDef) and n.name == "should_send_as_file")
    module = ast.Module(body=[ast.Import(names=[ast.alias(name="re", asname=None)]), func], type_ignores=[])
    ast.fix_missing_locations(module)
    ns = {}
    exec(compile(module, str(path), "exec"), ns)
    return ns["should_send_as_file"]

should_send_as_file = load_should_send_as_file()


class TestShouldSendAsFile:
    def test_code_block_with_ellipsis(self):
        text = "Here is code:\n```python\nprint('hi')\n...\n```"
        assert should_send_as_file(text) is True

    def test_plain_short_text(self):
        text = "Hello world"
        assert should_send_as_file(text) is False

    def test_long_snippet_starts_with_def(self):
        lines = ["def foo():", "    pass"] + ["# comment"] * 10
        text = "\n".join(lines)
        assert should_send_as_file(text) is True
