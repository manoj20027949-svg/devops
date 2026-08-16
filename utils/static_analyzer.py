"""
GitPulse - deterministic static code analyzer.

Runs real, verifiable checks on source code without calling an AI model:

  * Python: syntax validation (compile()), undefined-name detection (AST),
    missing/unused imports, exception-handling problems, dangerous calls.
  * JavaScript: unbalanced delimiters, unguarded JSON.parse / fetch,
    unchecked DOM element access.
  * Generic: hard-coded credential detection (masked, never echoed).

Every finding is normalized to the same shape the AI error explanation uses:

    {
        "severity":      critical | high | medium | low
        "file":          path
        "line":          int (0 when unknown)
        "error_type":    short machine-readable label
        "problem":       what is wrong (one line)
        "explanation":   why it happens
        "suggested_fix": how to solve it
        "auto_fixable":  bool - safe enough for the AI Fix workflow
        "confidence":    high | medium | low
        "fixed_code":    proposed replacement (or None)
        "engine":        "static"
    }

The existing regex CodeScanner produces compatible findings and can be merged
with these by the API layer.
"""

from __future__ import annotations

import ast
import builtins
import re
from typing import Any, Optional

# File extensions this analyzer understands.
PYTHON_EXTENSIONS = frozenset({".py", ".pyw"})
JAVASCRIPT_EXTENSIONS = frozenset({".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"})
ANALYZABLE_EXTENSIONS = PYTHON_EXTENSIONS | JAVASCRIPT_EXTENSIONS

# Best-effort names that are defined by the framework/app context and would
# otherwise produce false "undefined name" findings in real Flask projects.
_KNOWN_MODULE_NAMES = frozenset(
    {
        "app", "flask", "request", "session", "jsonify", "redirect", "render_template",
        "url_for", "flash", "g", "abort", "Response", "Blueprint", "Flask",
        "os", "sys", "json", "re", "time", "datetime", "timedelta", "pathlib",
        "Path", "logging", "logger", "requests", "urllib", "random", "math",
        "subprocess", "shutil", "tempfile", "hashlib", "base64", "sqlite3",
        "threading", "functools", "itertools", "collections", "typing", "Any",
        "Optional", "Dict", "List", "Tuple", "Set", "defaultdict", "Counter",
        "OrderedDict", "StringIO", "BytesIO", "io", "csv", "argparse",
        "pytest", "mock", "unittest", "dataclass", "field", "enum", "enum_type",
        "config", "settings", "get_logger", "dotenv", "load_dotenv", "authlib",
        "oauth", "github", "cryptography", "ssl", "socket", "email", "mimetypes",
        "wrapt", "markupsafe", "jinja2", "pandas", "numpy", "sklearn", "matplotlib",
        "plotly", "scipy", "tensorflow", "torch",
    }
)

# Deterministic regex rules for cross-language checks.
_SHELL_PATTERN = re.compile(
    r"os\.system\s*\(|subprocess\.(Popen|call|run)\s*\([^)]*\bshell\s*=\s*True",
    re.IGNORECASE,
)
_EVAL_PATTERN = re.compile(r"\b(eval|exec)\s*\(")
_BARE_EXCEPT_PATTERN = re.compile(r"\bexcept\s*:")
_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|api[_-]?key|client[_-]?secret|"
    r"access[_-]?token|auth[_-]?token|private[_-]?key)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
)
_DEBUG_PATTERN = re.compile(r"\bprint\s*\(|console\.(log|debug|info)\s*\(")


def _find_by_line(content: str, pattern: re.Pattern) -> list[int]:
    """Return 1-based line numbers where ``pattern`` matches."""
    return [content.count("\n", 0, m.start()) + 1 for m in pattern.finditer(content)]


def _mask_secret(text: str) -> str:
    """Mask credential-looking values so secrets never reach the UI."""
    return _SECRET_PATTERN.sub(
        lambda m: m.group(1).lower() + ' = "********"', text
    )


def _finding(
    severity: str,
    filename: str,
    line: int,
    error_type: str,
    problem: str,
    explanation: str,
    suggested_fix: str,
    auto_fixable: bool = False,
    confidence: str = "high",
    fixed_code: Optional[str] = None,
) -> dict[str, Any]:
    """Build a normalized finding dict (secrets in problem/explanation masked)."""
    return {
        "severity": severity,
        "file": filename,
        "line": line,
        "error_type": error_type,
        "problem": _mask_secret(problem),
        "explanation": _mask_secret(explanation),
        "suggested_fix": _mask_secret(suggested_fix),
        "auto_fixable": bool(auto_fixable),
        "confidence": confidence,
        "fixed_code": fixed_code,
        "engine": "static",
    }


# ----------------------------------------------------------------------
# Python analysis (AST based)
# ----------------------------------------------------------------------
def _collect_defined_names(tree: ast.AST) -> set[str]:
    """Best-effort set of every name assigned/imported/defined in the file."""
    defined: set[str] = set()
    targets: tuple[type[ast.AST], ...] = (
        ast.Name,
        ast.arg,
        ast.alias,
        ast.ExceptHandler,
    )

    class _Collector(ast.NodeVisitor):
        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802 (ast API)
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                defined.add(node.id)
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
            self.visit(node.value)

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            for t in node.targets:
                self.visit(t)
            self.visit(node.value)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            self.visit(node.target)
            if node.value:
                self.visit(node.value)

        def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
            self.visit(node.target)
            self.visit(node.value)

        def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
            defined.add(node.target.id)
            self.visit(node.value)

        def visit_comprehension(self, node: ast.comprehension) -> None:  # noqa: N802
            self.visit(node.target)
            self.visit(node.iter)
            for cond in node.ifs:
                self.visit(cond)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
            if node.name:
                defined.add(node.name)
            for b in node.body:
                self.visit(b)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            for a in node.names:
                defined.add(a.asname or a.name)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            defined.add(node.name)
            for a in node.args.args + node.args.kwonlyargs + node.args.posonlyargs:
                defined.add(a.arg)
            if node.args.vararg:
                defined.add(node.args.vararg.arg)
            if node.args.kwarg:
                defined.add(node.args.kwarg.arg)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.visit_FunctionDef(node)

        visit_Lambda = visit_FunctionDef  # lambda args are names too

    _Collector().visit(tree)
    return defined


def _unused_imports(filename: str, tree: ast.AST, content: str) -> list[dict]:
    """Report imports that are never referenced anywhere else in the file."""
    findings: list[dict] = []
    defined = _collect_defined_names(tree)

    class _ImportFinder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.imports: list[tuple[str, str, int]] = []  # (local_name, source, lineno)

        def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                self.imports.append((local, a.name, node.lineno))
            self.generic_visit(node)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
            for a in node.names:
                local = a.asname or a.name
                self.imports.append((local, f"{node.module}.{a.name}", node.lineno))
            self.generic_visit(node)

    finder = _ImportFinder()
    finder.visit(tree)
    for local, source, lineno in finder.imports:
        # Count occurrences of the bare local name (ignore the import line itself).
        count = 0
        for line_no, line in enumerate(content.splitlines(), start=1):
            if line_no == lineno:
                continue
            for match in re.finditer(rf"\b{re.escape(local)}\b", line):
                if not line[max(0, match.start() - 7) : match.start()].rstrip().endswith(
                    ("import", "from", "as")
                ):
                    count += 1
        if count == 0 and local != "*":
            findings.append(
                _finding(
                    severity="low",
                    filename=filename,
                    line=lineno,
                    error_type="UNUSED_IMPORT",
                    problem=f"Import '{local}' (from {source}) is never used.",
                    explanation=(
                        "The imported name is not referenced anywhere else in the file, "
                        "which adds noise and can mask which dependencies are real."
                    ),
                    suggested_fix=f"Remove the unused import `{local}`.",
                    auto_fixable=True,
                    confidence="medium",
                )
            )
    return findings


def analyze_python(filename: str, content: str) -> list[dict]:
    """Static Python checks: syntax, undefined names, bad exception handling."""
    findings: list[dict] = []

    # --- 1. Syntax validation (catches compile-time errors deterministically).
    try:
        compile(content, filename, "exec")
    except SyntaxError as exc:
        line = exc.lineno or 0
        findings.append(
            _finding(
                severity="critical",
                filename=filename,
                line=line,
                error_type="SYNTAX_ERROR",
                problem=f"Python syntax error: {exc.msg}",
                explanation=(
                    "The file does not compile, so it cannot run at all. "
                    "This usually means an unmatched bracket/quote or a missing colon."
                ),
                suggested_fix=(
                    f"Fix the syntax on line {line}: {exc.msg} "
                    f"({exc.text.strip() if exc.text else ''})."
                ),
                auto_fixable=False,
                confidence="high",
            )
        )
        return findings  # Deeper AST checks are meaningless on broken code.

    try:
        tree = ast.parse(content, filename=filename)
    except SyntaxError as exc:
        findings.append(
            _finding(
                severity="high",
                filename=filename,
                line=exc.lineno or 0,
                error_type="SYNTAX_ERROR",
                problem=f"Python parse error: {exc.msg}",
                explanation="The file could not be parsed into an AST.",
                suggested_fix="Fix the reported syntax problem before running checks.",
                auto_fixable=False,
                confidence="high",
            )
        )
        return findings

    # --- 2. Undefined names (best-effort, low confidence).
    defined = _collect_defined_names(tree)
    builtin_names = set(dir(builtins)) | _KNOWN_MODULE_NAMES

    class _NameFinder(ast.NodeVisitor):
        def __init__(self) -> None:
            self.loads: list[tuple[str, int]] = []

        def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
            if isinstance(node.ctx, ast.Load):
                self.loads.append((node.id, node.lineno))
            self.generic_visit(node)

        def visit_Attribute(self, node: ast.Attribute) -> None:  # noqa: N802
            # Do not walk attribute values (obj.method never defines obj).
            self.visit(node.value)

    finder = _NameFinder()
    finder.visit(tree)
    seen: set[tuple[str, int]] = set()
    for name, lineno in finder.loads:
        if (name, lineno) in seen:
            continue
        seen.add((name, lineno))
        if name in defined or name in builtin_names:
            continue
        findings.append(
            _finding(
                severity="medium",
                filename=filename,
                line=lineno,
                error_type="UNDEFINED_VARIABLE",
                problem=f"Possible undefined variable/name: `{name}`",
                explanation=(
                    f"`{name}` is used on line {lineno} but is never imported, "
                    "assigned or defined in this file. It would raise NameError at runtime."
                ),
                suggested_fix=(
                    f"Import or initialize `{name}` before line {lineno}, "
                    "or fix the typo if the name is misspelled."
                ),
                auto_fixable=False,
                confidence="low",
            )
        )

    # --- 3. Dangerous patterns.
    for line in _find_by_line(content, _SHELL_PATTERN):
        findings.append(
            _finding(
                severity="high",
                filename=filename,
                line=line,
                error_type="SHELL_INJECTION",
                problem="Potential shell injection: subprocess/os.system with untrusted input.",
                explanation=(
                    "Building shell commands from user input allows command injection. "
                    "Prefer passing an argument list with shell=False."
                ),
                suggested_fix="Replace shell commands with subprocess.run([...]) and shell=False.",
                auto_fixable=False,
                confidence="medium",
            )
        )
    for line in _find_by_line(content, _EVAL_PATTERN):
        findings.append(
            _finding(
                severity="high",
                filename=filename,
                line=line,
                error_type="EVAL_EXEC",
                problem="Use of eval()/exec(): executes arbitrary code at runtime.",
                explanation="eval/exec run any expression, which is an injection and maintenance risk.",
                suggested_fix="Use json.loads / ast.literal_eval for data, never eval user input.",
                auto_fixable=False,
                confidence="high",
            )
        )
    for line in _find_by_line(content, _BARE_EXCEPT_PATTERN):
        findings.append(
            _finding(
                severity="medium",
                filename=filename,
                line=line,
                error_type="BARE_EXCEPT",
                problem="Bare except: silently swallows all exceptions.",
                explanation="A bare except also catches SystemExit and KeyboardInterrupt, hiding real errors.",
                suggested_fix="Catch specific exception types and log them explicitly.",
                auto_fixable=False,
                confidence="high",
            )
        )

    # --- 4. Exception handling problems.
    class _ExceptVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.pass_only: list[int] = []

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
            # except ...: pass  -> swallows errors without any handling.
            if (
                len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
                and node.type is not None
            ):
                self.pass_only.append(node.lineno)
            self.generic_visit(node)

    except_visitor = _ExceptVisitor()
    except_visitor.visit(tree)
    for lineno in except_visitor.pass_only:
        findings.append(
            _finding(
                severity="medium",
                filename=filename,
                line=lineno,
                error_type="SWALLOWED_EXCEPTION",
                problem="Exception is caught but silently ignored (except ...: pass).",
                explanation="Errors are hidden, making failures extremely hard to debug.",
                suggested_fix="Log the exception or raise a meaningful error instead of passing.",
                auto_fixable=False,
                confidence="medium",
            )
        )

    # --- 5. Mutable default arguments.
    class _DefaultVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.mutable_defaults: list[int] = []

        def _visit_defaults(self, node: ast.arguments, default_lineno: int) -> None:  # helper
            for d in node.defaults:
                if isinstance(d, (ast.List, ast.Dict, ast.Set, ast.Call)):
                    self.mutable_defaults.append(getattr(d, "lineno", default_lineno))
            for d in node.kw_defaults:
                if d is not None and isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    self.mutable_defaults.append(getattr(d, "lineno", default_lineno))

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            self._visit_defaults(node.args, node.lineno)
            self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    default_visitor = _DefaultVisitor()
    default_visitor.visit(tree)
    for lineno in default_visitor.mutable_defaults:
        findings.append(
            _finding(
                severity="medium",
                filename=filename,
                line=lineno,
                error_type="MUTABLE_DEFAULT",
                problem="Mutable default argument: shared across all calls.",
                explanation="A list/dict default is created once and mutated between calls, leaking state.",
                suggested_fix="Use None as the default and initialize the mutable inside the function.",
                auto_fixable=False,
                confidence="high",
            )
        )

    # --- 6. Unused imports.
    findings.extend(_unused_imports(filename, tree, content))

    return findings


# ----------------------------------------------------------------------
# JavaScript analysis
# ----------------------------------------------------------------------
_OPEN_TO_CLOSE = {"{": "}", "(": ")", "[": "]"}


def _delimiter_errors(content: str) -> list[dict]:
    """Find unbalanced brackets/parens/braces and report a location."""
    stack: list[tuple[str, int]] = []
    line = 1
    in_string: Optional[str] = None
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == "\n":
            line += 1
            i += 1
            continue
        if in_string:
            if ch == "\\":
                i += 2
                continue
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            in_string = ch
            i += 1
            continue
        if ch in _OPEN_TO_CLOSE:
            stack.append((ch, line))
        elif ch in _OPEN_TO_CLOSE.values():
            if not stack:
                return [
                    _finding(
                        severity="high",
                        filename="",
                        line=line,
                        error_type="UNBALANCED_DELIMITER",
                        problem=f"Unexpected closing delimiter '{ch}'.",
                        explanation="There is no matching opening bracket before it - syntax is broken.",
                        suggested_fix="Fix the unmatched bracket around this line.",
                        confidence="high",
                    )
                ]
            open_ch, open_line = stack.pop()
            if _OPEN_TO_CLOSE[open_ch] != ch:
                return [
                    _finding(
                        severity="high",
                        filename="",
                        line=line,
                        error_type="UNBALANCED_DELIMITER",
                        problem=(
                            f"Mismatched delimiter: '{open_ch}' opened on line "
                            f"{open_line} but closed with '{ch}' here."
                        ),
                        explanation="Mismatched brackets produce JavaScript syntax errors.",
                        suggested_fix="Match every opening bracket with its correct closing bracket.",
                        confidence="high",
                    )
                ]
        i += 1
    if stack:
        open_ch, open_line = stack[-1]
        return [
            _finding(
                severity="high",
                filename="",
                line=open_line,
                error_type="UNBALANCED_DELIMITER",
                problem=f"Unclosed '{open_ch}' opened on line {open_line}.",
                explanation="The file has an unclosed bracket - it will not parse.",
                suggested_fix="Close the bracket before the end of the file.",
                confidence="high",
            )
        ]
    return []


def _json_parse_errors(filename: str, content: str) -> list[dict]:
    """Flag JSON.parse(...) calls that are not guarded by try/catch."""
    findings: list[dict] = []
    for match in re.finditer(r"JSON\.parse\s*\(", content):
        line = content.count("\n", 0, match.start()) + 1
        head = content[: match.start()]
        # Look for an enclosing try/catch before this occurrence.
        last_try = head.rfind("try")
        last_catch = head.rfind("catch")
        guarded = last_try > last_catch  # a try still open when JSON.parse runs
        if not guarded:
            findings.append(
                _finding(
                    severity="medium",
                    filename=filename,
                    line=line,
                    error_type="UNGUARDED_JSON_PARSE",
                    problem="JSON.parse() without try/catch error handling.",
                    explanation=(
                        "Malformed JSON from a server or file raises an exception that "
                        "is not caught, breaking the whole script."
                    ),
                    suggested_fix="Wrap JSON.parse in a try/catch (or .then().catch()) and handle bad data.",
                    auto_fixable=False,
                    confidence="medium",
                )
            )
    return findings


def _fetch_errors(filename: str, content: str) -> list[dict]:
    """Flag fetch(...) calls with no error handling path."""
    findings: list[dict] = []
    for match in re.finditer(r"\bfetch\s*\(", content):
        line = content.count("\n", 0, match.start()) + 1
        tail = content[match.start() : match.start() + 400]
        # Heuristic: an immediate .catch(...) after the call chain, or the
        # call is inside a try block (await fetch).
        head = content[: match.start()]
        has_catch = ".catch(" in tail[: tail.find("\n") or len(tail)]
        last_try = head.rfind("try")
        last_catch = head.rfind("catch")
        in_try = last_try > last_catch
        if not has_catch and not in_try:
            findings.append(
                _finding(
                    severity="medium",
                    filename=filename,
                    line=line,
                    error_type="UNHANDLED_FETCH",
                    problem="fetch() without error handling (.catch or try/catch).",
                    explanation=(
                        "Network failures or non-2xx responses silently reject the "
                        "promise; without .catch the UI shows no feedback."
                    ),
                    suggested_fix="Append .catch(...) or use try/catch around await fetch() and render the error.",
                    auto_fixable=False,
                    confidence="medium",
                )
            )
    return findings


def _dom_guard_errors(filename: str, content: str) -> list[dict]:
    """Flag DOM element lookups whose result is used without a null guard."""
    findings: list[dict] = []
    for match in re.finditer(
        r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*document\.getElementById\s*\(",
        content,
    ):
        var_name = match.group(1)
        line = content.count("\n", 0, match.start()) + 1
        # Anywhere in the file a guard like `if (var)` or `if (!var)` exists?
        guarded = bool(
            re.search(
                rf"if\s*\(\s*!?\s*{re.escape(var_name)}\s*[)!=<>\s]", content
            )
        )
        if not guarded:
            findings.append(
                _finding(
                    severity="low",
                    filename=filename,
                    line=line,
                    error_type="UNCHECKED_DOM_ELEMENT",
                    problem=(
                        f"`{var_name}` from getElementById is used without a null guard."
                    ),
                    explanation=(
                        "getElementById returns null when the element is missing; "
                        "accessing properties on null throws a TypeError."
                    ),
                    suggested_fix=(
                        f"Guard the lookup: if (!{var_name}) return; before using it."
                    ),
                    auto_fixable=False,
                    confidence="low",
                )
            )
    return findings


def analyze_javascript(filename: str, content: str) -> list[dict]:
    """Static JavaScript checks (structural + async error handling)."""
    findings: list[dict] = []
    for finding in _delimiter_errors(content):
        if finding.get("file") == "":
            finding["file"] = filename
        findings.append(finding)
    findings.extend(_json_parse_errors(filename, content))
    findings.extend(_fetch_errors(filename, content))
    findings.extend(_dom_guard_errors(filename, content))
    return findings


# ----------------------------------------------------------------------
# Generic / cross-language checks
# ----------------------------------------------------------------------
def _secret_errors(filename: str, content: str) -> list[dict]:
    """Detect hard-coded secrets. The actual value is never included."""
    findings: list[dict] = []
    for match in _SECRET_PATTERN.finditer(content):
        line = content.count("\n", 0, match.start()) + 1
        findings.append(
            _finding(
                severity="critical",
                filename=filename,
                line=line,
                error_type="HARDCODED_SECRET",
                problem="Hard-coded credential detected (value masked).",
                explanation=(
                    "A password, token or API key appears directly in source code. "
                    "Anyone with repository access can read and reuse it."
                ),
                suggested_fix=(
                    "Move the value to environment variables or a secret manager, "
                    "rotate the leaked value immediately, and add the file to .gitignore."
                ),
                auto_fixable=False,
                confidence="high",
            )
        )
    return findings


def _flask_errors(filename: str, content: str) -> list[dict]:
    """Insecure Flask configuration checks (debug in prod, hard-coded secret)."""
    findings: list[dict] = []
    for match in re.finditer(r"(?i)FLASK_DEBUG\s*=\s*['\"]?1['\"]?", content):
        line = content.count("\n", 0, match.start()) + 1
        findings.append(
            _finding(
                severity="medium",
                filename=filename,
                line=line,
                error_type="INSECURE_FLASK_CONFIG",
                problem="FLASK_DEBUG is enabled (debug mode).",
                explanation=(
                    "Debug mode exposes an interactive traceback/debugger to anyone "
                    "who can reach the server, a serious security risk in production."
                ),
                suggested_fix="Set FLASK_DEBUG=0 in production and use FLASK_DEBUG only locally.",
                auto_fixable=False,
                confidence="high",
            )
        )
    return findings


# ----------------------------------------------------------------------
# Entry points
# ----------------------------------------------------------------------
def analyze_content(filename: str, content: str) -> list[dict]:
    """Run every applicable deterministic check on one file's content."""
    findings: list[dict] = []
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in PYTHON_EXTENSIONS:
        findings.extend(analyze_python(filename, content))
    elif ext in JAVASCRIPT_EXTENSIONS:
        findings.extend(analyze_javascript(filename, content))
    # Secrets can hide in any file (configs, .env, scripts, minified bundles),
    # so the check runs on everything that looks like text.
    if not isinstance(content, bytes):
        findings.extend(_secret_errors(filename, content))
    # Debug output only makes sense for source files.
    if ext in ANALYZABLE_EXTENSIONS:
        findings.extend(_debug_errors(filename, content))
    # Insecure config checks apply to settings/config files regardless of ext.
    base = filename.rsplit("/", 1)[-1].lower()
    if base in (".env", ".env.example") or "settings" in base:
        findings.extend(_flask_errors(filename, content))
    return findings


def _debug_errors(filename: str, content: str) -> list[dict]:
    """Flag debug print/console.log statements left in source files."""
    findings: list[dict] = []
    for match in _DEBUG_PATTERN.finditer(content):
        line = content.count("\n", 0, match.start()) + 1
        findings.append(
            _finding(
                severity="low",
                filename=filename,
                line=line,
                error_type="DEBUG_PRINT",
                problem="Debug output left in code (print/console.log).",
                explanation="Debug statements are usually removed before shipping.",
                suggested_fix="Replace with structured logging or remove before committing.",
                auto_fixable=True,
                confidence="medium",
            )
        )
    return findings


def merge_findings(
    *finding_lists: list[dict],
) -> list[dict]:
    """Merge several finding lists, deduplicating identical (type, file, line)."""
    merged: list[dict] = []
    seen: set[tuple[str, str, int]] = set()
    for findings in finding_lists:
        for finding in findings:
            key = (
                str(finding.get("error_type") or finding.get("rule_id") or ""),
                str(finding.get("file") or finding.get("filename") or ""),
                int(finding.get("line") or finding.get("line_number") or 0),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(finding)
    return merged


def severity_sort_key(finding: dict) -> int:
    """Sort findings so CRITICAL appears first."""
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    return order.get(str(finding.get("severity") or "").lower(), 99)


def summarize(findings: list[dict]) -> dict[str, int]:
    """Count findings per severity."""
    summary = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": len(findings)}
    for finding in findings:
        key = str(finding.get("severity") or "").upper()
        if key in summary:
            summary[key] += 1
    return summary
