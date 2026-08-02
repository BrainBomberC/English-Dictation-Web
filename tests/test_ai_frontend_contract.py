import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AiFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javascript = (ROOT / "static" / "js" / "app.js").read_text(
            encoding="utf-8"
        )
        cls.template = (ROOT / "templates" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_completion_budget_and_truncation_detection_are_enabled(self):
        self.assertIn("max_tokens:4096", self.javascript)
        self.assertIn("choice.finish_reason", self.javascript)
        self.assertIn("isIncompleteAnswer", self.javascript)
        self.assertIn("appendContinuation", self.javascript)

    def test_internal_reasoning_is_never_used_as_the_answer(self):
        self.assertNotIn("msg.reasoning_content", self.javascript)
        self.assertNotIn("lines.slice(-3)", self.javascript)
        self.assertIn("Final Review against Constraints", self.javascript)
        self.assertIn("cleanFinalAnswer", self.javascript)

    def test_http_errors_are_checked_before_rendering(self):
        self.assertIn("response.ok", self.javascript)
        self.assertIn("AI 服务请求失败", self.javascript)

    def test_model_is_optional_for_local_and_hosted_apis(self):
        self.assertIn('id="api-model"', self.template)
        self.assertIn('if (model) body.model = model', self.javascript)

    def test_template_requests_the_updated_script(self):
        self.assertIn('/static/js/app.js?v=63', self.template)


if __name__ == "__main__":
    unittest.main()
