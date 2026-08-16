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


class TestCommitAnalysis:
    def _commit(self, **overrides):
        commit = {
            "sha": "abc123",
            "author": "alice",
            "date": "2024-01-01T00:00:00Z",
            "message": "Add CSV export endpoint",
            "files": [{"filename": "src/export.py"}],
            "stats": {"additions": 10, "deletions": 2},
        }
        commit.update(overrides)
        return commit

    def test_normal_commit(self):
        result = ai_analyzer.classify_commit(self._commit())
        assert result["category"] == "Normal"
        assert result["flagged"] is False

    def test_large_commit(self):
        files = [{"filename": f"src/f{i}.py"} for i in range(15)]
        result = ai_analyzer.classify_commit(
            self._commit(files=files, message="Refactor file handling module")
        )
        assert result["category"] == "Large"
        assert result["flagged"] is True

    def test_high_line_count_is_large(self):
        result = ai_analyzer.classify_commit(
            self._commit(stats={"additions": 1200, "deletions": 0},
                         message="Add comprehensive test coverage")
        )
        assert result["category"] == "Large"

    def test_bugprone_with_fix_message_and_deletion(self):
        result = ai_analyzer.classify_commit(
            self._commit(
                message="hotfix: fix crash on empty input",
                stats={"additions": 2, "deletions": 30},
            )
        )
        assert result["category"] == "Bug-prone"
        assert result["flagged"] is True

    def test_risky_when_touching_sensitive_files(self):
        result = ai_analyzer.classify_commit(
            self._commit(files=[{"filename": "config/settings.py"}],
                         message="Bump config version safely")
        )
        assert result["category"] == "Risky"
        assert any("sensitive" in r for r in result["reasons"])

    def test_suspicious_when_generic_message(self):
        result = ai_analyzer.classify_commit(self._commit(message="update"))
        assert result["category"] == "Suspicious"

    def test_suspicious_when_no_author(self):
        result = ai_analyzer.classify_commit(self._commit(author=""))
        assert result["category"] == "Suspicious"

    def test_short_message_is_suspicious(self):
        result = ai_analyzer.classify_commit(self._commit(message="Add CSV"))
        assert result["category"] == "Suspicious"

    def test_analyze_commits_flags_first(self):
        commits = [
            self._commit(sha="1", message="add feature"),
            self._commit(sha="2", message="wip"),
        ]
        analyzed = ai_analyzer.analyze_commits(commits)
        assert analyzed[0]["flagged"] is True  # Suspicious sorted first


class TestMemberAnalysis:
    def test_contribution_percentage(self):
        members = [
            {"username": "alice", "commits": 30},
            {"username": "bob", "commits": 10},
        ]
        analyzed = ai_analyzer.analyze_members(members)
        assert analyzed[0]["username"] == "alice"
        assert analyzed[0]["contribution_pct"] == 75.0
        assert analyzed[1]["contribution_pct"] == 25.0

    def test_zero_commits_does_not_divide_by_zero(self):
        analyzed = ai_analyzer.analyze_members([{"username": "alice", "commits": 0}])
        assert analyzed[0]["contribution_pct"] == 0.0
        assert "contribution_pct" in analyzed[0]

    def test_activity_level(self):
        assert ai_analyzer._activity_level({"commits": 20, "last_active_days": 2}) == "High activity"
        assert ai_analyzer._activity_level({"commits": 6, "last_active_days": 1}) == "Moderate activity"
        assert ai_analyzer._activity_level({"commits": 2, "last_active_days": 30}) == "Low activity"


class TestHealthCategory:
    def test_excellent(self):
        assert ai_analyzer.health_category(95) == "Excellent"

    def test_good(self):
        assert ai_analyzer.health_category(72) == "Good"

    def test_needs_attention(self):
        assert ai_analyzer.health_category(50) == "Needs Attention"

    def test_critical(self):
        assert ai_analyzer.health_category(30) == "Critical"


class TestAnalyzeRepositoryDeep:
    def test_deep_adds_analyses(self):
        report = {
            "overview": {"members": 1, "inactive_members": 0},
            "members": [{"username": "alice", "commits": 5, "pr_count": 1, "last_active_days": 2}],
            "pushes": [
                {
                    "sha": "abc",
                    "author": "alice",
                    "message": "wip",
                    "files": [{"filename": "a.py"}],
                    "stats": {"additions": 1, "deletions": 0},
                }
            ],
            "pull_requests": [],
            "issues": [],
            "contributors": [],
        }
        result = ai_analyzer.analyze_repository(report, deep=True)
        assert "health_score" in result
        assert "health_label" in result
        assert isinstance(result["commit_analyses"], list)
        assert result["commit_analyses"][0]["sha"] == "abc"
        assert isinstance(result["member_analyses"], list)
        assert result["member_analyses"][0]["username"] == "alice"

    def test_shallow_missing_report_optional_keys(self):
        result = ai_analyzer.analyze_repository({}, deep=False)
        assert "health_score" in result
        assert "health_label" in result
        assert "commit_analyses" not in result
