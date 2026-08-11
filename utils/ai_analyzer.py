"""
GitPulse - AI coaching engine.

Two sources of suggestions:

1. Rule-based engine (always available, zero dependencies). It derives
   coaching from commit frequency, inactivity, PR count and issue
   participation using transparent heuristics.

2. Claude (Anthropic) engine (optional). When `ANTHROPIC_API_KEY` is set,
   developer metrics are sent to Claude and it returns personalized
   coaching as JSON. Failures fall back to the rule-based suggestions so
   the dashboard always has content to show.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from config.logging_setup import get_logger
from config.settings import settings

logger = get_logger("app")

# Time-to-live for the in-memory suggestion cache (seconds).
CACHE_TTL_SECONDS = 600

# Maximum number of characters sent to the AI per file.
MAX_CODE_CHARS = 8000


@dataclass
class CoachingSuggestion:
    """A single coaching recommendation for one developer."""

    member: str
    category: str
    summary: str
    detail: str
    priority: str  # HIGH | MEDIUM | LOW

    def to_dict(self) -> dict[str, str]:
        return {
            "member": self.member,
            "category": self.category,
            "summary": self.summary,
            "detail": self.detail,
            "priority": self.priority,
        }


# ----------------------------------------------------------------------
# Rule-based engine
# ----------------------------------------------------------------------
class RuleBasedAnalyzer:
    """Generates deterministic coaching suggestions from metrics."""

    def analyze(self, members: list[dict[str, Any]]) -> list[CoachingSuggestion]:
        suggestions: list[CoachingSuggestion] = []
        for member in members:
            suggestions.extend(self._suggest_for(member))
        # Most important first.
        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        suggestions.sort(key=lambda s: priority_order.get(s.priority, 9))
        return suggestions

    def _suggest_for(self, member: dict[str, Any]) -> list[CoachingSuggestion]:
        """Build coaching suggestions for one developer."""
        suggestions: list[CoachingSuggestion] = []
        username = member.get("username", "unknown")
        commits = member.get("commits", 0)
        prs = member.get("pr_count", 0)
        issues = member.get("issue_count", 0)
        inactive_days = member.get("last_active_days")

        # --- Inactive developer ---
        if inactive_days is not None and inactive_days > 14:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Inactivity",
                    summary=f"{username} has been inactive for {inactive_days} days.",
                    detail=(
                        f"No commits detected in the last {inactive_days} days. "
                        "Check in with them about blockers, vacation, or role changes."
                    ),
                    priority="HIGH",
                )
            )

        # --- Low commit frequency ---
        if commits == 0:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Commit Activity",
                    summary=f"{username} has no commits in the analyzed window.",
                    detail=(
                        "Zero commits recorded. Verify they are tracked with the correct "
                        "email in git config, or investigate engagement."
                    ),
                    priority="MEDIUM",
                )
            )
        elif commits < 10:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Commit Activity",
                    summary=f"{username} shows low commit frequency ({commits} commits).",
                    detail=(
                        "Fewer than 10 commits in 90 days suggests shallow engagement. "
                        "Encourage smaller, more frequent merges and weekly check-ins."
                    ),
                    priority="MEDIUM",
                )
            )

        # --- Review / PR participation ---
        if prs == 0 and commits > 0:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Code Review",
                    summary=f"{username} has no open pull requests.",
                    detail=(
                        "They are committing but not opening PRs. Confirm their branch "
                        "strategy and encourage early PRs for visibility."
                    ),
                    priority="LOW",
                )
            )

        # --- Issue engagement ---
        if issues == 0 and commits > 0:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Issue Participation",
                    summary=f"{username} has no open issues assigned.",
                    detail=(
                        "No issue participation detected. Encourage triage duty and "
                        "pairing on bug-fixes to broaden context."
                    ),
                    priority="LOW",
                )
            )

        # --- High performer (recognition) ---
        if commits >= 30 and inactive_days is not None and inactive_days <= 7:
            suggestions.append(
                CoachingSuggestion(
                    member=username,
                    category="Recognition",
                    summary=f"{username} is a high performer ({commits} commits).",
                    detail=(
                        "Strong sustained output. Consider recognizing them publicly and "
                        "checking they are not at burnout risk."
                    ),
                    priority="LOW",
                )
            )

        return suggestions


# ----------------------------------------------------------------------
# Claude (Anthropic) engine
# ----------------------------------------------------------------------
class ClaudeAnalyzer:
    """Sends developer metrics to Claude and parses coaching JSON."""

    def __init__(self) -> None:
        import anthropic  # imported lazily so the fallback works without the SDK

        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.model = settings.ANTHROPIC_MODEL

    def chat(self, system: str, prompt: str, max_tokens: int = 2000) -> Optional[str]:
        """
        Send a single chat turn to Claude and return the text response.

        Returns None on any API failure (network, auth, malformed model).
        The caller decides how to fall back.
        """
        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=0.4,
                system=system,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(
                block.text for block in response.content if getattr(block, "type", "") == "text"
            )
            return text.strip() or None
        except Exception as exc:  # noqa: BLE001 - fall back on any API error
            logger.warning("Claude request failed: %s", exc)
            return None

    def _build_prompt(self, members: list[dict[str, Any]]) -> str:
        """Create a strict, self-contained prompt for Claude."""
        compact = [
            {
                "username": m.get("username"),
                "commits_90d": m.get("commits"),
                "open_prs": m.get("pr_count"),
                "open_issues": m.get("issue_count"),
                "days_since_last_commit": m.get("last_active_days"),
                "activity_score": m.get("activity_score"),
            }
            for m in members
        ]
        return (
            "You are a senior engineering manager coach. Based ONLY on the GitHub "
            "metrics below, write short, actionable coaching suggestions.\n"
            "Respond with a JSON array only, no markdown, in this exact shape:\n"
            '{"suggestions": [{"member": "<username>", "category": "<one of: '
            'Commit Activity|Inactivity|Code Review|Issue Participation|Recognition|'
            'General>", "summary": "<one short sentence>", '
            '"detail": "<1-2 sentences, actionable>", "priority": "HIGH|MEDIUM|LOW"}]}\n'
            "Metrics:\n"
            + json.dumps(compact)
        )

    def analyze(self, members: list[dict[str, Any]]) -> list[CoachingSuggestion]:
        """Ask Claude for coaching; returns an empty list on any failure."""
        if not members:
            return []

        text = self.chat(
            system=(
                "You are a senior engineering manager coach. Respond with JSON only."
            ),
            prompt=self._build_prompt(members),
        )
        if not text:
            return []
        return self._parse_response(text)

    @staticmethod
    def _parse_response(text: str) -> list[CoachingSuggestion]:
        """Defensively parse Claude's JSON array response."""
        text = text.strip()
        # Strip markdown code fences if the model wrapped the JSON.
        if text.startswith("```"):
            text = text.strip("`")
            first_brace = text.find("{")
            last_brace = text.rfind("}")
            if first_brace != -1 and last_brace != -1:
                text = text[first_brace : last_brace + 1]

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return []

        raw_items = payload.get("suggestions", []) if isinstance(payload, dict) else payload
        if not isinstance(raw_items, list):
            return []

        suggestions: list[CoachingSuggestion] = []
        for item in raw_items:
            if not isinstance(item, dict) or not item.get("member"):
                continue
            suggestions.append(
                CoachingSuggestion(
                    member=str(item.get("member")),
                    category=str(item.get("category", "General")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    priority=str(item.get("priority", "LOW")).upper(),
                )
            )
        return suggestions


# ----------------------------------------------------------------------
# Facade with caching
# ----------------------------------------------------------------------
_cache: dict[str, tuple[float, list[CoachingSuggestion]]] = {}


def generate_suggestions(
    members: list[dict[str, Any]],
    use_ai: bool = True,
) -> list[CoachingSuggestion]:
    """
    Generate coaching suggestions for the whole team.

    Strategy:
      1. Rule-based suggestions are ALWAYS produced (guaranteed output).
      2. If Claude is configured AND `use_ai` is True, ask Claude and merge
         its results in front of the rule-based ones.
      3. Results are cached in-memory for CACHE_TTL_SECONDS to avoid
         burning API credits on every page load.

    Returns:
        A list of CoachingSuggestion objects.
    """
    cache_key = hashlib.sha256(json.dumps(members, default=str).encode()).hexdigest()

    cached = _cache.get(cache_key)
    if cached and (time.time() - cached[0]) < CACHE_TTL_SECONDS:
        logger.debug("Serving coaching suggestions from cache.")
        return cached[1]

    rule_based = RuleBasedAnalyzer().analyze(members)

    ai_suggestions: list[CoachingSuggestion] = []
    if use_ai and settings.anthropic_configured:
        ai_suggestions = ClaudeAnalyzer().analyze(members)
        if ai_suggestions:
            logger.info("Claude produced %d coaching suggestions.", len(ai_suggestions))
        else:
            logger.info("Claude returned nothing; keeping rule-based suggestions.")

    merged = ai_suggestions + rule_based
    _cache[cache_key] = (time.time(), merged)
    return merged


# ======================================================================
# AI code / PR / issue analysis (AI Error Detection + Auto-Fix input)
# ======================================================================

def _extract_json(text: str) -> Any:
    """
    Defensively extract a JSON payload from a Claude response.

    Handles markdown code fences and stray prose around the JSON object.
    Returns None when no valid JSON can be found.
    """
    if not text:
        return None
    text = text.strip()
    # Strip a ```json ... ``` fence if present.
    if text.startswith("```"):
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            text = text[first_brace : last_brace + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: pull the first balanced {...} block out of the text.
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : i + 1]
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    return None
    return None


def _ai_result_or_none(prompt: str, system: str) -> Optional[dict]:
    """Ask Claude for a JSON dict; returns None on any failure."""
    if not settings.anthropic_configured:
        return None
    text = ClaudeAnalyzer().chat(system=system, prompt=prompt, max_tokens=3000)
    payload = _extract_json(text) if text else None
    if isinstance(payload, dict):
        return payload
    return None


# ----------------------------------------------------------------------
# Rule-based code analysis fallback (uses the existing regex scanner)
# ----------------------------------------------------------------------
def rule_based_code_analysis(filename: str, content: str) -> dict:
    """
    Analyze a single file with the existing regex CodeScanner and format
    the worst finding into the AI result shape so the frontend and the
    fix workflow can always consume a uniform dict.
    """
    from utils.code_scanner import CodeScanner
    from utils.code_scanner import SCANNABLE_EXTENSIONS, _rules_for, _scan_content

    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext not in SCANNABLE_EXTENSIONS:
        return {
            "severity": "low",
            "file": filename,
            "line": 0,
            "error_type": "none",
            "problem": "No obvious issue detected by the rule-based scanner.",
            "explanation": "The file was scanned with the built-in regex rules.",
            "suggested_fix": "",
            "fixed_code": None,
            "engine": "rule-based",
        }

    findings = _scan_content(filename, content, _rules_for(ext))
    findings.sort(key=CodeScanner.severity_sort_key)
    if not findings:
        return {
            "severity": "low",
            "file": filename,
            "line": 0,
            "error_type": "none",
            "problem": "No obvious issue detected by the rule-based scanner.",
            "explanation": "The file was scanned with the built-in regex rules.",
            "suggested_fix": "",
            "fixed_code": None,
            "engine": "rule-based",
        }
    top = findings[0]
    return {
        "severity": top.severity.lower(),
        "file": top.filename,
        "line": top.line_number,
        "error_type": top.rule_id,
        "problem": top.description,
        "explanation": top.description,
        "suggested_fix": top.recommendation,
        "fixed_code": None,
        "engine": "rule-based",
    }


# ----------------------------------------------------------------------
# Public analysis entry points
# ----------------------------------------------------------------------
def analyze_code(filename: str, content: str, context: str = "") -> dict:
    """
    AI-powered analysis of a single code file.

    Returns a dict with keys:
        severity, file, line, error_type, problem, explanation,
        suggested_fix, fixed_code, engine

    Falls back to the rule-based scanner when Anthropic is unavailable or
    returns something unusable, so callers always get a dict.
    """
    fallback = rule_based_code_analysis(filename, content)

    if not settings.anthropic_configured:
        return fallback

    prompt = (
        "Analyze the following source file for bugs, syntax errors, runtime "
        "errors, logic errors, security issues, code quality problems and "
        "missing error handling.\n"
        f"Context: {context}\n"
        f"File: {filename}\n"
        "```\n"
        f"{content[:MAX_CODE_CHARS]}\n"
        "```\n"
        "Respond with a single JSON object only, no markdown:\n"
        '{"severity": "high|medium|low", "file": "<path>", "line": <int>, '
        '"error_type": "<short label>", "problem": "<one line>", '
        '"explanation": "<1-2 sentences>", "suggested_fix": "<description>", '
        '"fixed_code": "<complete corrected file content or empty string>"}\n'
        "If there is nothing wrong, set severity to \"low\", error_type to "
        "\"none\" and fixed_code to an empty string."
    )
    payload = _ai_result_or_none(
        prompt,
        system=(
            "You are a senior code reviewer and static-analysis engineer. "
            "Only output the requested JSON object."
        ),
    )
    if not payload:
        return fallback

    result = {
        "severity": str(payload.get("severity", fallback.get("severity", "low"))).lower(),
        "file": str(payload.get("file") or filename),
        "line": int(payload.get("line") or 0),
        "error_type": str(payload.get("error_type") or "unknown"),
        "problem": str(payload.get("problem") or fallback.get("problem", "")),
        "explanation": str(payload.get("explanation") or ""),
        "suggested_fix": str(payload.get("suggested_fix") or ""),
        "fixed_code": payload.get("fixed_code") or "",
        "engine": "ai",
    }
    return result


def analyze_pull_request(pr: dict, diff: str = "") -> dict:
    """
    AI analysis of a pull request: quality, bugs, security, complexity,
    and suggested improvements. Falls back to a rule-based summary.
    """
    fallback = {
        "severity": "low",
        "problem": "No AI analysis available.",
        "explanation": "ANTHROPIC_API_KEY is not configured; showing a rule-based summary.",
        "suggestions": [],
        "engine": "rule-based",
    }

    if not settings.anthropic_configured:
        return fallback

    prompt = (
        "Analyze this pull request. Provide code quality notes, potential "
        "bugs, security concerns, complexity assessment and suggested "
        "improvements. Be constructive.\n"
        f"Title: {pr.get('title', '')}\n"
        f"Body: {pr.get('body', '')[:1000]}\n"
        f"Changed files: {pr.get('changed_files', 0)} | "
        f"Additions: {pr.get('additions', 0)} | Deletions: {pr.get('deletions', 0)}\n"
        "Diff (truncated):\n"
        f"```\n{diff[:MAX_CODE_CHARS]}\n```\n"
        "Respond with a single JSON object:\n"
        '{"severity": "high|medium|low", "problem": "<one line>", '
        '"explanation": "<1-2 sentences>", '
        '"suggestions": ["<suggestion 1>", "<suggestion 2>"]}'
    )
    payload = _ai_result_or_none(
        prompt,
        system=(
            "You are a senior pull-request reviewer. Only output the requested JSON object."
        ),
    )
    if not payload:
        return fallback
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list):
        suggestions = []
    return {
        "severity": str(payload.get("severity", "medium")).lower(),
        "problem": str(payload.get("problem", "")),
        "explanation": str(payload.get("explanation", "")),
        "suggestions": [str(s) for s in suggestions],
        "engine": "ai",
    }


def analyze_issue(issue: dict) -> dict:
    """
    AI analysis of a GitHub issue: summary, likely root cause, suggested
    solution, related files, and implementation steps.
    """
    fallback = {
        "severity": "medium",
        "summary": str(issue.get("title", "")),
        "root_cause": "No AI analysis available.",
        "solution": "Review the issue and reproduce before fixing.",
        "related_files": [],
        "steps": [],
        "engine": "rule-based",
    }

    if not settings.anthropic_configured:
        return fallback

    prompt = (
        "Analyze this GitHub issue and help a developer triage it.\n"
        f"Title: {issue.get('title', '')}\n"
        f"Body: {issue.get('body', '')[:2000]}\n"
        f"Labels: {', '.join(issue.get('labels', []))}\n"
        "Respond with a single JSON object:\n"
        '{"severity": "high|medium|low", "summary": "<one line>", '
        '"root_cause": "<likely root cause>", "solution": "<suggested solution>", '
        '"related_files": ["<file>", ...], "steps": ["<step 1>", "<step 2>", ...]}'
    )
    payload = _ai_result_or_none(
        prompt,
        system=(
            "You are a senior software engineer triaging GitHub issues. "
            "Only output the requested JSON object."
        ),
    )
    if not payload:
        return fallback

    def _as_list(key: str) -> list[str]:
        value = payload.get(key, [])
        return [str(v) for v in value] if isinstance(value, list) else []

    return {
        "severity": str(payload.get("severity", "medium")).lower(),
        "summary": str(payload.get("summary") or issue.get("title", "")),
        "root_cause": str(payload.get("root_cause", "")),
        "solution": str(payload.get("solution", "")),
        "related_files": _as_list("related_files"),
        "steps": _as_list("steps"),
        "engine": "ai",
    }
