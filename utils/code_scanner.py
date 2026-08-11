"""
GitPulse - rule-based static code scanner.

A lightweight, dependency-free code scanner built on regular expressions.
It is intentionally simple: it flags suspicious patterns and always lets
a human make the final call. It is NOT a replacement for real SAST tools
like Semgrep, Bandit or CodeQL.

Scan targets:
  * Local directories (walked recursively).
  * GitHub repositories (files fetched via the git trees API).

Each finding includes: file name, line number, severity, description and
a concrete recommendation, exactly as required by the dashboard.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Union

from config.logging_setup import get_logger

logger = get_logger("scanner")

# File extensions we know how to analyze.
SCANNABLE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb"}

# GitHub repos are huge; cap the amount of code we pull.
MAX_GITHUB_FILES = 300
MAX_FILE_SIZE_BYTES = 512 * 1024  # skip files larger than 512 KB

# Files whose *contents define the rules* must never be scanned: their rule
# patterns and recommendation text literally contain the trigger strings
# (e.g. `eval(`, `except:`, "TODO") and would always self-flag. Excluded by
# basename so the same protection applies to local and GitHub scans.
SELF_SCAN_EXCLUSIONS = frozenset({os.path.basename(__file__)})

# Test files/directories are skipped by default: fixtures legitimately contain
# fake secrets and trigger strings, so findings there are almost always noise
# (real SAST tools apply the same default). Both can be overridden per call.
TEST_DIR_NAMES = frozenset({"tests", "test", "spec", "testing"})
DEFAULT_EXCLUDE_DIRS = frozenset({"node_modules", "venv", ".venv", "dist", "build"})


def _is_test_file(filename: str) -> bool:
    """True for pytest-style test modules and test config."""
    name = filename.lower()
    return (
        name == "conftest.py"
        or name == "test.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


@dataclass(frozen=True)
class Rule:
    """A single detection rule."""

    rule_id: str
    severity: str  # CRITICAL | HIGH | MEDIUM | LOW
    extensions: set[str]
    pattern: re.Pattern
    description: str
    recommendation: str

    def describe(self, filename: str, line_no: int) -> str:
        return f"{filename}:{line_no} | {self.rule_id} | {self.severity}"


@dataclass
class Finding:
    """One detection result."""

    rule_id: str
    severity: str
    filename: str
    line_number: int
    line_content: str
    description: str
    recommendation: str

    def to_dict(self) -> dict:
        """Serialize for templates / JSON endpoints."""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "filename": self.filename,
            "line_number": self.line_number,
            "line_content": self.line_content.strip()[:120],
            "description": self.description,
            "recommendation": self.recommendation,
        }


# ----------------------------------------------------------------------
# Rule definitions
# ----------------------------------------------------------------------
def _compile(pattern: str, flags: int = re.IGNORECASE) -> re.Pattern:
    return re.compile(pattern, flags)


RULES: list[Rule] = [
    Rule(
        rule_id="HARDCODED_SECRET",
        severity="CRITICAL",
        extensions={".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb"},
        pattern=_compile(
            r"(?i)(password|passwd|secret|api[_-]?key|client[_-]?secret|"
            r"access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][^'\"]{8,}['\"]"
        ),
        description="Possible hard-coded secret (password / API key / token).",
        recommendation="Move the value to environment variables or a secret manager, and rotate the leaked value immediately.",
    ),
    Rule(
        rule_id="SQL_INJECTION",
        severity="HIGH",
        extensions={".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb"},
        pattern=_compile(
            r"\.execute\s*\(\s*[\"']SELECT|f['\"]SELECT.*\bWHERE\b.*\{|"
            r"query\s*[:=]\s*[\"'].*\bwhere\b.*%\s*\(|"
            r"(\+\s*|%\()\s*\w+\s*(\))?\s*\)?\s*\)\s*;?\s*$",
            re.IGNORECASE,
        ),
        description="Potential SQL injection: user input may be concatenated into a query.",
        recommendation="Use parameterized queries / prepared statements and an ORM instead of string interpolation.",
    ),
    Rule(
        rule_id="EVAL_USAGE",
        severity="HIGH",
        extensions={".py", ".js", ".ts", ".jsx", ".tsx"},
        pattern=_compile(r"\beval\s*\("),
        description="Use of eval(): executes arbitrary code at runtime.",
        recommendation="Remove eval(). Use safer alternatives (json.loads, ast.literal_eval, Function constructor only for trusted data).",
    ),
    Rule(
        rule_id="SHELL_INJECTION",
        severity="CRITICAL",
        extensions={".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".go", ".rb"},
        pattern=_compile(
            r"os\.system\s*\(|subprocess\.(Popen|call|run)\s*\([^)]*\bshell\s*=\s*True|"
            r"child_process\.exec(File)?\s*\(|Runtime\.getRuntime\(\)\.exec|"
            r"system\(|\`.*\$\{.*\}\`"
        ),
        description="Potential shell injection: command constructed from untrusted input.",
        recommendation="Avoid shell=True / shell interpreters. Pass arguments as a list and validate input strictly.",
    ),
    Rule(
        rule_id="BARE_EXCEPT",
        severity="MEDIUM",
        extensions={".py"},
        pattern=_compile(r"\bexcept\s*:"),
        description="Bare except: silently swallows all exceptions including SystemExit/KeyboardInterrupt.",
        recommendation="Catch specific exception types and handle/log them explicitly.",
    ),
    Rule(
        rule_id="MUTABLE_DEFAULT",
        severity="MEDIUM",
        extensions={".py"},
        pattern=_compile(
            r"def\s+\w+\s*\([^)]*=\s*(\[\s*\]|\{\s*\}|set\(\))"
        ),
        description="Mutable default argument: shared across calls and can leak state.",
        recommendation="Use None as the default and initialize the mutable inside the function body.",
    ),
    Rule(
        rule_id="DANGEROUSLYSETHTML",
        severity="HIGH",
        extensions={".js", ".ts", ".jsx", ".tsx"},
        pattern=_compile(r"\.innerHTML\s*=|dangerouslySetInnerHTML\s*="),
        description="Rendering raw HTML: opens the door to XSS attacks.",
        recommendation="Use textContent / React's children instead, or sanitize the HTML with a library like DOMPurify.",
    ),
    Rule(
        rule_id="CONSOLE_LOG",
        severity="LOW",
        extensions={".js", ".ts", ".jsx", ".tsx"},
        pattern=_compile(r"console\.(log|debug|info)\s*\("),
        description="Debug logging left in production code.",
        recommendation="Remove debug logs or route them through a proper logging framework.",
    ),
    Rule(
        rule_id="PRINT_DEBUG",
        severity="LOW",
        extensions={".py", ".java", ".go", ".rb"},
        pattern=_compile(r"\bprint\s*\(|System\.out\.println|puts\s+\w+"),
        description="Debug print statement left in production code.",
        recommendation="Replace with structured logging (e.g. Python logging module).",
    ),
    Rule(
        rule_id="TODO_FIXME",
        severity="LOW",
        extensions=SCANNABLE_EXTENSIONS,
        pattern=_compile(r"TODO|FIXME|HACK|XXX"),
        description="Left-over marker comment indicating unfinished work.",
        recommendation="Resolve the task and remove the marker, or track it in your issue tracker.",
    ),
]

# Fast lookup: extension -> rules that apply to it.
_RULES_BY_EXTENSION: dict[str, list[Rule]] = {}
for rule in RULES:
    for ext in rule.extensions:
        _RULES_BY_EXTENSION.setdefault(ext, []).append(rule)


def _rules_for(extension: str) -> list[Rule]:
    """Return the rules that apply to a given file extension."""
    return _RULES_BY_EXTENSION.get(extension, [])


def _scan_content(
    filename: str,
    content: str,
    rules: list[Rule],
    line_offset: int = 0,
) -> list[Finding]:
    """Run every rule against a single file's content."""
    findings: list[Finding] = []
    for rule in rules:
        for match in rule.pattern.finditer(content):
            # Compute the 1-based line number for this match.
            line_number = content.count("\n", 0, match.start()) + 1 + line_offset
            line_start = content.rfind("\n", 0, match.start()) + 1
            line_end = content.find("\n", match.end())
            if line_end == -1:
                line_end = len(content)
            line_content = content[line_start:line_end]

            findings.append(
                Finding(
                    rule_id=rule.rule_id,
                    severity=rule.severity,
                    filename=filename,
                    line_number=line_number,
                    line_content=line_content,
                    description=rule.description,
                    recommendation=rule.recommendation,
                )
            )
    return findings


class CodeScanner:
    """Scans local paths and GitHub repositories for risky patterns."""

    # ------------------------------------------------------------------
    # Local filesystem scanning
    # ------------------------------------------------------------------
    def scan_path(
        self,
        root: str,
        include_hidden: bool = False,
        max_files: int = 500,
        exclude_files: Optional[set[str]] = None,
        exclude_tests: bool = True,
    ) -> list[Finding]:
        """
        Recursively scan a local directory.

        Args:
            root:           Absolute path to scan.
            include_hidden: Whether to include dot-directories (e.g. .git).
            max_files:      Hard cap to avoid runaway scans.
            exclude_files:  Basenames to skip (rule-definition files that
                            would otherwise always self-flag).
            exclude_tests:  Skip test files and directories (default True).
        """
        exclude_files = set(exclude_files or SELF_SCAN_EXCLUSIONS)
        findings: list[Finding] = []
        scanned = 0

        for dirpath, dirnames, filenames in os.walk(root):
            if not include_hidden:
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS]
            if exclude_tests:
                dirnames[:] = [d for d in dirnames if d not in TEST_DIR_NAMES]

            for filename in filenames:
                if filename in exclude_files:
                    continue
                if exclude_tests and _is_test_file(filename):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext not in SCANNABLE_EXTENSIONS:
                    continue

                full_path = os.path.join(dirpath, filename)
                try:
                    if os.path.getsize(full_path) > MAX_FILE_SIZE_BYTES:
                        continue
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as fh:
                        content = fh.read()
                except OSError as exc:
                    logger.warning("Skipping %s: %s", full_path, exc)
                    continue

                findings.extend(_scan_content(full_path, content, _rules_for(ext)))
                scanned += 1
                if scanned >= max_files:
                    logger.warning("Reached max_files (%d); stopping scan.", max_files)
                    break
            if scanned >= max_files:
                break

        logger.info(
            "Scanned %d files under %s -> %d findings",
            scanned, root, len(findings),
        )
        return findings

    # ------------------------------------------------------------------
    # GitHub repository scanning
    # ------------------------------------------------------------------
    def scan_github_repo(
        self,
        api: object,
        owner: str,
        repo: str,
        branch: str = "HEAD",
        max_files: int = MAX_GITHUB_FILES,
        exclude_files: Optional[set[str]] = None,
        exclude_tests: bool = True,
    ) -> list[Finding]:
        """
        Scan a remote GitHub repository using the git trees API.

        Args:
            api:           A GitHubAPI instance (used to fetch the tree + blobs).
            owner:         Repository owner.
            repo:          Repository name.
            branch:        Branch ref to scan (defaults to the default branch).
            exclude_files: Basenames to skip (rule-definition files that
                           would otherwise always self-flag).
            exclude_tests: Skip test files and directories (default True).
        """
        exclude_files = set(exclude_files or SELF_SCAN_EXCLUSIONS)
        findings: list[Finding] = []
        try:
            # 1. Get the recursive file tree for the branch.
            tree = api._request(
                "GET", f"/repos/{owner}/{repo}/git/trees/{branch}", params={"recursive": "1"}
            ).get("tree", [])
        except Exception as exc:  # noqa: BLE001 - scanner must never crash a route
            logger.error("Could not fetch git tree for %s/%s: %s", owner, repo, exc)
            return findings

        blobs = [
            item
            for item in tree
            if item.get("type") == "blob"
            and item.get("path", "").split("/")[-1] not in {"package-lock.json", "yarn.lock", "poetry.lock", "Pipfile.lock"}
        ][:max_files]

        for item in blobs:
            path = item.get("path", "")
            path_segments = path.split("/")
            if os.path.basename(path) in exclude_files:
                continue
            if exclude_tests and (
                _is_test_file(path_segments[-1])
                or any(seg in TEST_DIR_NAMES for seg in path_segments[:-1])
            ):
                continue
            ext = os.path.splitext(path)[1].lower()
            if ext not in SCANNABLE_EXTENSIONS:
                continue
            if (item.get("size") or 0) > MAX_FILE_SIZE_BYTES:
                continue

            try:
                blob = api._request("GET", item["url"])
                content = blob.get("content", "")
                if blob.get("encoding") == "base64":
                    import base64
                    content = base64.b64decode(content).decode("utf-8", errors="ignore")
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipping %s: %s", path, exc)
                continue

            findings.extend(_scan_content(path, content, _rules_for(ext)))

        logger.info("Scanned GitHub repo %s/%s -> %d findings", owner, repo, len(findings))
        return findings

    # ------------------------------------------------------------------
    # Reporting helpers
    # ------------------------------------------------------------------
    @staticmethod
    def summarize(findings: list[Finding]) -> dict[str, int]:
        """Count findings per severity for the dashboard summary cards."""
        summary = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "TOTAL": len(findings)}
        for finding in findings:
            severity = finding.severity
            if severity in summary:
                summary[severity] += 1
        return summary

    @staticmethod
    def severity_sort_key(finding: Finding) -> int:
        """Sort so CRITICAL appears first in tables."""
        order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        return order.get(finding.severity, 99)
