"""Tests for the deterministic static code analyzer."""

from utils import static_analyzer


class TestPythonChecks:
    def test_detects_syntax_error(self):
        findings = static_analyzer.analyze_content("broken.py", "def foo(:\n    pass\n")
        types = [f["error_type"] for f in findings]
        assert "SYNTAX_ERROR" in types
        syntax = next(f for f in findings if f["error_type"] == "SYNTAX_ERROR")
        assert syntax["severity"] == "critical"
        assert syntax["engine"] == "static"
        assert syntax["line"] >= 1

    def test_detects_undefined_variable(self):
        findings = static_analyzer.analyze_content("app.py", "def run():\n    return missing_name\n")
        assert any(f["error_type"] == "UNDEFINED_VARIABLE" for f in findings)

    def test_detects_shell_injection(self):
        findings = static_analyzer.analyze_content("run.py", 'import os\nos.system(user_input)\n')
        assert any(f["error_type"] == "SHELL_INJECTION" for f in findings)

    def test_detects_bare_except(self):
        findings = static_analyzer.analyze_content("x.py", "try:\n    pass\nexcept:\n    pass\n")
        assert any(f["error_type"] == "BARE_EXCEPT" for f in findings)

    def test_detects_mutable_default(self):
        findings = static_analyzer.analyze_content("x.py", "def f(items=[]):\n    return items\n")
        assert any(f["error_type"] == "MUTABLE_DEFAULT" for f in findings)

    def test_detects_eval_usage(self):
        findings = static_analyzer.analyze_content("x.py", "result = eval(data)\n")
        assert any(f["error_type"] == "EVAL_EXEC" for f in findings)

    def test_detects_unused_import(self):
        findings = static_analyzer.analyze_content("x.py", "import os\n\ndef f():\n    return 1\n")
        assert any(f["error_type"] == "UNUSED_IMPORT" for f in findings)

    def test_detects_swallowed_exception(self):
        findings = static_analyzer.analyze_content("x.py", "try:\n    risky()\nexcept Exception:\n    pass\n")
        assert any(f["error_type"] == "SWALLOWED_EXCEPTION" for f in findings)

    def test_clean_python_produces_no_critical(self):
        code = (
            "import os\n\n"
            "def greet(name):\n"
            "    return f'Hello {name}'\n\n"
            "if __name__ == '__main__':\n"
            "    print(greet(os.getenv('USER')))\n"
        )
        findings = static_analyzer.analyze_content("clean.py", code)
        assert not any(f["severity"] in ("critical", "high") for f in findings)


class TestJavaScriptChecks:
    def test_detects_unbalanced_delimiter(self):
        findings = static_analyzer.analyze_content("app.js", "function broken() {\n  if (x) {\n}\n")
        assert any(f["error_type"] == "UNBALANCED_DELIMITER" for f in findings)

    def test_detects_unguarded_json_parse(self):
        findings = static_analyzer.analyze_content("app.js", "const data = JSON.parse(raw);\n")
        assert any(f["error_type"] == "UNGUARDED_JSON_PARSE" for f in findings)

    def test_detects_unhandled_fetch(self):
        findings = static_analyzer.analyze_content("app.js", 'fetch("/api/items")\n')
        assert any(f["error_type"] == "UNHANDLED_FETCH" for f in findings)

    def test_fetch_with_catch_is_clean(self):
        findings = static_analyzer.analyze_content(
            "app.js", 'fetch("/api/items").then(r => r.json()).catch(e => console.error(e));\n'
        )
        assert not any(f["error_type"] == "UNHANDLED_FETCH" for f in findings)

    def test_detects_unchecked_dom_element(self):
        findings = static_analyzer.analyze_content(
            "app.js", "const btn = document.getElementById('save');\nbtn.click();\n"
        )
        assert any(f["error_type"] == "UNCHECKED_DOM_ELEMENT" for f in findings)

    def test_balanced_js_has_no_delimiter_error(self):
        code = "function f() {\n  const x = [1, 2, { a: 1 }];\n  return x;\n}\n"
        findings = static_analyzer.analyze_content("app.js", code)
        assert not any(f["error_type"] == "UNBALANCED_DELIMITER" for f in findings)


class TestSecretMasking:
    def test_masks_hardcoded_secrets(self):
        findings = static_analyzer.analyze_content(".env", 'api_key = "sk-live-1234567890abcdef"\n')
        secret = next(f for f in findings if f["error_type"] == "HARDCODED_SECRET")
        assert secret["severity"] == "critical"
        # The real value must never appear in any field.
        joined = " ".join(
            str(v) for v in secret.values() if isinstance(v, str)
        )
        assert "sk-live-1234567890abcdef" not in joined
        assert "masked" in joined

    def test_masks_secret_even_in_python_file(self):
        findings = static_analyzer.analyze_content(
            "config.py", 'PASSWORD = "hunter2supersecret"\n'
        )
        secret = next(f for f in findings if f["error_type"] == "HARDCODED_SECRET")
        assert "hunter2supersecret" not in str(secret)

    def test_flask_debug_flag(self):
        findings = static_analyzer.analyze_content(".env", "FLASK_DEBUG = \"1\"\n")
        assert any(f["error_type"] == "INSECURE_FLASK_CONFIG" for f in findings)


class TestHelpers:
    def test_summarize_counts(self):
        findings = [
            {"severity": "critical"},
            {"severity": "high"},
            {"severity": "medium"},
            {"severity": "low"},
        ]
        summary = static_analyzer.summarize(findings)
        assert summary["CRITICAL"] == 1
        assert summary["HIGH"] == 1
        assert summary["MEDIUM"] == 1
        assert summary["LOW"] == 1
        assert summary["TOTAL"] == 4

    def test_merge_findings_deduplicates(self):
        a = [{"error_type": "X", "file": "f.py", "line": 1}]
        b = [{"error_type": "X", "file": "f.py", "line": 1}, {"error_type": "Y", "file": "f.py", "line": 2}]
        merged = static_analyzer.merge_findings(a, b)
        assert len(merged) == 2

    def test_severity_sort_puts_critical_first(self):
        findings = [
            {"severity": "low"},
            {"severity": "critical"},
            {"severity": "high"},
        ]
        sorted_findings = sorted(findings, key=static_analyzer.severity_sort_key)
        assert sorted_findings[0]["severity"] == "critical"
        assert sorted_findings[-1]["severity"] == "low"

    def test_unknown_extension_gets_no_checks(self):
        findings = static_analyzer.analyze_content("notes.txt", "not code at all\n")
        assert findings == []
