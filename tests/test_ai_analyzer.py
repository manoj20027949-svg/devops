"""Tests for the AI coaching engine (rule-based + Claude parsing)."""

import json

from utils import ai_analyzer
from utils.ai_analyzer import ClaudeAnalyzer, RuleBasedAnalyzer, generate_suggestions


class TestRuleBased:
    def _member(self, **overrides):
        base = {
            "username": "alice",
            "commits": 20,
            "pr_count": 1,
            "issue_count": 1,
            "last_active_days": 3,
        }
        base.update(overrides)
        return base

    def test_flags_inactivity_as_high_priority(self):
        suggestions = RuleBasedAnalyzer()._suggest_for(self._member(last_active_days=30))
        assert any(s.category == "Inactivity" and s.priority == "HIGH" for s in suggestions)

    def test_flags_zero_commits(self):
        suggestions = RuleBasedAnalyzer()._suggest_for(self._member(commits=0))
        assert any(s.category == "Commit Activity" for s in suggestions)

    def test_flags_low_commit_frequency(self):
        suggestions = RuleBasedAnalyzer()._suggest_for(self._member(commits=5))
        assert any(s.summary and "low commit frequency" in s.summary for s in suggestions)

    def test_recognizes_high_performer(self):
        suggestions = RuleBasedAnalyzer()._suggest_for(self._member(commits=40))
        assert any(s.category == "Recognition" for s in suggestions)

    def test_analyze_sorts_priority_first(self):
        suggestions = RuleBasedAnalyzer().analyze(
            [self._member(last_active_days=30), self._member(commits=1)]
        )
        order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        priorities = [order[s.priority] for s in suggestions]
        assert priorities == sorted(priorities)


class TestClaudeParsing:
    def test_parses_fenced_json(self):
        text = (
            '```json\n{"suggestions": [{"member": "alice", "category": "General", '
            '"summary": "s", "detail": "d", "priority": "high"}]}\n```'
        )

        out = ClaudeAnalyzer._parse_response(text)

        assert len(out) == 1
        assert out[0].member == "alice"
        assert out[0].priority == "HIGH"

    def test_returns_empty_for_invalid_json(self):
        assert ClaudeAnalyzer._parse_response("this is not json") == []

    def test_skips_items_without_member(self):
        payload = json.dumps({"suggestions": [{"category": "General"}]})
        assert ClaudeAnalyzer._parse_response(payload) == []


class TestGenerateSuggestions:
    def test_returns_rule_based_when_ai_disabled(self, monkeypatch):
        monkeypatch.setattr(ai_analyzer.settings, "ANTHROPIC_API_KEY", "")
        members = [{"username": "alice", "commits": 0}]

        suggestions = generate_suggestions(members)

        assert suggestions, "expected at least a rule-based suggestion"

    def test_results_are_cached(self, monkeypatch):
        monkeypatch.setattr(ai_analyzer.settings, "ANTHROPIC_API_KEY", "")
        members = [{"username": "bob", "commits": 0, "last_active_days": None}]

        first = generate_suggestions(members)
        second = generate_suggestions(members)

        assert first is second


class TestExtractJson:
    def test_extracts_fenced_json(self):
        assert ai_analyzer._extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_extracts_json_with_prose_around_it(self):
        assert ai_analyzer._extract_json('Here you go: {"a": 1} hope that helps') == {"a": 1}

    def test_returns_none_for_garbage(self):
        assert ai_analyzer._extract_json("not json at all") is None

    def test_returns_none_for_empty(self):
        assert ai_analyzer._extract_json("") is None


class TestAnalyzeCode:
    def test_rule_based_fallback_when_ai_disabled(self, monkeypatch):
        monkeypatch.setattr(ai_analyzer.settings, "ANTHROPIC_API_KEY", "")
        result = ai_analyzer.analyze_code("app.py", "print(1)\n")
        assert result["engine"] == "rule-based"
        assert "severity" in result and "fixed_code" in result

    def test_result_shape_has_all_keys(self):
        result = ai_analyzer.analyze_code("app.py", "x = 1\n")
        for key in ("severity", "file", "line", "error_type", "problem", "explanation", "suggested_fix", "fixed_code", "engine"):
            assert key in result, f"missing key {key}"


class TestAnalyzePullRequest:
    def test_fallback_shape_when_ai_disabled(self, monkeypatch):
        monkeypatch.setattr(ai_analyzer.settings, "ANTHROPIC_API_KEY", "")
        result = ai_analyzer.analyze_pull_request({"title": "x", "body": "", "changed_files": 1, "additions": 2, "deletions": 0})
        assert result["engine"] == "rule-based"
        assert "suggestions" in result


class TestAnalyzeIssue:
    def test_fallback_shape_when_ai_disabled(self, monkeypatch):
        monkeypatch.setattr(ai_analyzer.settings, "ANTHROPIC_API_KEY", "")
        result = ai_analyzer.analyze_issue({"title": "Crash", "body": "details", "labels": []})
        assert result["engine"] == "rule-based"
        assert result["summary"] == "Crash"
        assert "steps" in result
