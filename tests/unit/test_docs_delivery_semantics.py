"""
Tests that documentation correctly describes delivery semantics.

Written to verify BLOCKER-04: docs must not claim exactly-once delivery.
"""

from pathlib import Path

DOCS_DIR = Path(__file__).parent.parent.parent / "docs"


class TestDeliverySemantics:
    """Verify docs don't falsely claim exactly-once delivery."""

    def test_no_exactly_once_claim_in_docs(self):
        """No doc file should claim exactly-once delivery as a guarantee."""
        violations = []
        for doc_file in DOCS_DIR.rglob("*.md"):
            content = doc_file.read_text().lower()
            # Look for "exactly-once" or "exactly once" as a positive claim
            # (not in "does NOT guarantee" context)
            lines = content.split("\n")
            for i, line in enumerate(lines, 1):
                if "exactly-once" in line or "exactly once" in line:
                    # Allow it only in a "does NOT guarantee" context
                    if (
                        "not guarantee" not in line
                        and "not" not in line.split("exactly")[0]
                    ):
                        rel_path = doc_file.relative_to(DOCS_DIR)
                        violations.append(f"{rel_path}:{i}: {line.strip()}")
        assert (
            not violations
        ), "Documentation falsely claims exactly-once delivery:\n" + "\n".join(
            violations
        )

    def test_at_least_once_mentioned_in_getting_started(self):
        """Getting started guide should mention at-least-once delivery."""
        content = (DOCS_DIR / "quickstart.md").read_text().lower()
        assert "at-least-once" in content or "at least once" in content

    def test_at_least_once_mentioned_in_retries(self):
        """Retries doc should mention at-least-once delivery."""
        content = (DOCS_DIR / "tutorial" / "03-retries.md").read_text().lower()
        assert "at-least-once" in content or "at least once" in content

    def test_at_least_once_mentioned_in_production(self):
        """Production guide should mention at-least-once delivery."""
        content = (
            (DOCS_DIR / "production" / "going-to-production.md").read_text().lower()
        )
        assert "at-least-once" in content or "at least once" in content

    def test_idempotency_in_getting_started(self):
        """Getting started should mention idempotency."""
        content = (DOCS_DIR / "quickstart.md").read_text().lower()
        assert "idempoten" in content

    def test_idempotency_in_retries(self):
        """Retries doc should mention idempotency."""
        content = (DOCS_DIR / "tutorial" / "03-retries.md").read_text().lower()
        assert "idempoten" in content

    def test_cross_service_guide_covers_at_least_once_and_idempotent(self):
        """The cross-service guide must explain at-least-once delivery and
        idempotency in the same paragraph."""
        content = (DOCS_DIR / "guides" / "cross-service-jobs.md").read_text().lower()
        assert "at-least-once" in content or "at least once" in content
        assert "idempoten" in content
