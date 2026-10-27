import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest import mock
import urllib.error

import pages_artifact_ready as ready


class ArtifactContract(unittest.TestCase):
    def setUp(self):
        self.artifact = {
            "id": 123, "name": "github-pages-42-3", "expired": False,
            "workflow_run": {"head_sha": "expected-head"},
        }
        self.clock = 0

    def select(self, artifacts, artifact_id=123, head="expected-head"):
        return ready.select_artifact({"artifacts": artifacts}, self.artifact["name"], artifact_id, head)

    def pause(self, seconds):
        self.clock += seconds

    def wait(self, fetch):
        return ready.wait_ready(fetch, self.artifact["name"], 123, "expected-head", lambda: self.clock, self.pause)

    def test_missing_metadata_stays_unready(self):
        self.assertIsNone(self.select([]))

    def test_exact_uploaded_artifact_is_accepted(self):
        self.assertEqual(self.select([self.artifact]), self.artifact)

    def test_other_attempts_cannot_poison_current_name(self):
        old = dict(self.artifact, name="github-pages")
        self.assertEqual(self.select([old, old, self.artifact]), self.artifact)

    def test_duplicate_current_name_fails(self):
        with self.assertRaisesRegex(RuntimeError, "exactly one"):
            self.select([self.artifact, self.artifact])

    def test_wrong_id_and_revision_fail(self):
        for kwargs in ({"artifact_id": 124}, {"head": "other-head"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(RuntimeError):
                self.select([self.artifact], **kwargs)

    def test_expired_or_unknown_expiry_fails(self):
        for state in (True, None):
            with self.subTest(state=state), self.assertRaisesRegex(RuntimeError, "expiry|expired"):
                self.select([dict(self.artifact, expired=state)])

    def test_delayed_visibility_waits_for_exact_artifact(self):
        responses = iter([{"artifacts": []}, {"artifacts": []}, {"artifacts": [self.artifact]}])
        self.assertEqual(self.wait(lambda _: next(responses)), self.artifact)
        self.assertEqual(self.clock, 4)

    def test_absence_is_bounded(self):
        with self.assertRaisesRegex(TimeoutError, "60 seconds"):
            self.wait(lambda _: {"artifacts": []})
        self.assertEqual(self.clock, 60)

    def test_permission_error_is_not_retried(self):
        error = urllib.error.HTTPError("https://api.github.com", 403, "Denied", {}, None)
        self.addCleanup(error.close)
        with self.assertRaises(urllib.error.HTTPError):
            self.wait(mock.Mock(side_effect=error))
        self.assertEqual(self.clock, 0)

    def test_transient_failure_is_retried_within_budget(self):
        error = urllib.error.HTTPError("https://api.github.com", 503, "Unavailable", {}, None)
        fetch = mock.Mock(side_effect=[error, {"artifacts": [self.artifact]}])
        self.assertEqual(self.wait(fetch), self.artifact)
        self.assertEqual(self.clock, 2)

    def test_transport_failure_is_retried_within_budget(self):
        fetch = mock.Mock(side_effect=[urllib.error.URLError("fixture"), {"artifacts": [self.artifact]}])
        self.assertEqual(self.wait(fetch), self.artifact)
        self.assertEqual(self.clock, 2)

    def test_pagination_uses_a_shared_request_budget(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, timeout))
            self.pause(2)
            batch = [self.artifact] * 100 if len(calls) == 1 else []
            return io.StringIO(json.dumps({"artifacts": batch}))

        result = ready.list_run_artifacts("https://api.github.com/run/artifacts", "fixture", 5, opener, lambda: self.clock)
        self.assertEqual(len(result["artifacts"]), 100)
        self.assertEqual([call[1] for call in calls], [5, 3])
        self.assertTrue(calls[1][0].endswith("page=2"))

    def test_pagination_cannot_reset_the_deadline(self):
        def opener(_request, timeout):
            self.pause(timeout)
            return io.StringIO(json.dumps({"artifacts": [self.artifact] * 100}))

        with self.assertRaises(TimeoutError):
            ready.list_run_artifacts("https://api.github.com/run/artifacts", "fixture", 5, opener, lambda: self.clock)
        self.assertEqual(self.clock, 5)

    def test_access_probe_does_not_upload_or_deploy(self):
        env = {"GITHUB_API_URL": "https://api.github.com", "GITHUB_REPOSITORY": "owner/repo",
               "GITHUB_RUN_ID": "42", "GH_TOKEN": "fixture"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(ready, "list_run_artifacts", return_value={"artifacts": []}) as fetch:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                ready.main(["--probe-access"])
        self.assertEqual(fetch.call_count, 1)
        self.assertIn("Read-only metadata access verified", output.getvalue())
        self.assertNotIn("fixture", output.getvalue())

    def test_workflow_preserves_release_contract(self):
        source = (Path(__file__).parents[1] / "workflows/pages.yml").read_text()
        self.assertIn("cancel-in-progress: false", source)
        self.assertIn("permissions:\n  contents: read\n  pages: write\n  id-token: write", source)
        self.assertIn("needs: contract\n    if: github.event_name != 'pull_request'", source)
        self.assertIn("PAGES_ARTIFACT_NAME: github-pages-${{ github.run_id }}-${{ github.run_attempt }}", source)
        self.assertEqual(source.count("${{ env.PAGES_ARTIFACT_NAME }}"), 2)
        self.assertIn("EXPECTED_ARTIFACT_ID: ${{ steps.upload.outputs.artifact_id }}", source)
        self.assertIn("timeout-minutes: 2", source)
        for pin in ("3d3c42e5aac5ba805825da76410c181273ba90b1", "45bfe0192ca1faeb007ade9deae92b16b8254a0d",
                    "fc324d3547104276b827a68afc52ff2a11cc49c9", "cd2ce8fcbc39b97be8ca5fce6e763baed58fa128"):
            self.assertIn("@" + pin, source)
        stage = source.split("      - name: Stage site\n", 1)[1].split("      - name: Configure Pages\n", 1)[0]
        # Only the root README is excluded; package preservation has behavioral coverage.
        self.assertEqual(hashlib.sha256(stage.encode()).hexdigest(), 'b0142a4d5264caa58097445cf7fbec79f2eff96d31441d4a73932907a5125b47')


if __name__ == "__main__":
    unittest.main()
