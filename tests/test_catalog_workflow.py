from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / "examples" / "catalog-test-runtime.yml"


def run(command, cwd, *, env=None, check=True):
    return subprocess.run(
        command,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        text=True,
        capture_output=True,
        check=check,
    )


def step_block(name: str) -> tuple[str, str]:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    marker = f"      - name: {name}"
    start = lines.index(marker)
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("      - ")),
        len(lines),
    )
    section = lines[start:end]
    condition_lines = []
    if "        if: >-" in section:
        condition_start = section.index("        if: >-") + 1
        for line in section[condition_start:]:
            if line and not line.startswith("          "):
                break
            condition_lines.append(line[10:] if line.startswith("          ") else "")
    script_lines = []
    if "        run: |" in section:
        script_start = section.index("        run: |") + 1
        for line in section[script_start:]:
            if line and not line.startswith("          "):
                break
            script_lines.append(line[10:] if line.startswith("          ") else "")
    if not script_lines:
        raise AssertionError(f"no literal run block for {name}")
    return "\n".join(condition_lines), "\n".join(script_lines) + "\n"


class CatalogWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = Path(tempfile.mkdtemp(prefix="watchtower-workflow-"))
        self.addCleanup(shutil.rmtree, self.temp)
        self.origin = self.temp / "origin.git"
        run(["git", "init", "--bare", "--quiet", str(self.origin)], self.temp)
        run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], self.origin)
        self.repo = self.temp / "runtime"
        run(["git", "clone", "--quiet", str(self.origin), str(self.repo)], self.temp)
        run(["git", "config", "user.name", "test"], self.repo)
        run(["git", "config", "user.email", "test@example.invalid"], self.repo)
        (self.repo / ".github").mkdir()
        (self.repo / "state").mkdir()
        (self.repo / "README.md").write_text(
            "# WATCHTOWER CATALOG TEST RUNTIME\n", encoding="utf-8"
        )
        (self.repo / "state" / ".gitkeep").touch()
        run(["git", "add", "."], self.repo)
        run(["git", "commit", "--quiet", "-m", "initial"], self.repo)
        run(["git", "branch", "-M", "main"], self.repo)
        run(["git", "push", "--quiet", "origin", "main"], self.repo)

    def checkpoint_env(self, output: Path) -> dict[str, str]:
        return {
            "CHECKPOINT_OPERATION": "run",
            "GITHUB_RUN_ID": "1234",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_SHA": run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip(),
            "GITHUB_OUTPUT": str(output),
        }

    def acquire_checkpoint(self):
        _, script = step_block("Acquire persistent run checkpoint")
        output = self.temp / "output"
        result = run(["bash", "-c", script], self.repo, env=self.checkpoint_env(output))
        return result, output

    def test_ownership_guards_are_exact_and_fail_closed(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        baseline_if, _ = step_block("Establish silent baseline only from empty test state")
        monitor_if, _ = step_block("Run against persistent test state and test channel")
        persist_if, _ = step_block("Persist only test state")
        self.assertIn("steps.checkpoint.outputs.owned == 'true'", baseline_if)
        self.assertIn("steps.checkpoint.outputs.owned == 'true'", monitor_if)
        self.assertIn("steps.checkpoint.outputs.owned == 'true'", persist_if)
        self.assertIn('echo "owned=true" >> "$GITHUB_OUTPUT"', text)
        self.assertLess(text.index("git push --quiet origin HEAD:main"), text.index('echo "owned=true"'))

    def test_existing_checkpoint_neither_claims_nor_changes_remote(self):
        marker = self.repo / "state" / "_workflow_checkpoint.json"
        marker.write_text('{"run_id":"old"}\n', encoding="utf-8")
        run(["git", "add", str(marker.relative_to(self.repo))], self.repo)
        run(["git", "commit", "--quiet", "-m", "old checkpoint"], self.repo)
        run(["git", "push", "--quiet", "origin", "main"], self.repo)
        before = run(["git", "rev-parse", "refs/heads/main"], self.origin).stdout.strip()
        _, script = step_block("Acquire persistent run checkpoint")
        output = self.temp / "blocked-output"
        result = run(
            ["bash", "-c", script], self.repo,
            env=self.checkpoint_env(output), check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertEqual(before, run(["git", "rev-parse", "refs/heads/main"], self.origin).stdout.strip())

    def test_successful_checkpoint_is_owned_only_after_push_and_survives_clone(self):
        _, output = self.acquire_checkpoint()
        self.assertEqual(output.read_text(encoding="utf-8"), "owned=true\n")
        clone = self.temp / "next"
        run(["git", "clone", "--quiet", str(self.origin), str(clone)], self.temp)
        self.assertTrue((clone / "state" / "_workflow_checkpoint.json").is_file())

    def test_baseline_with_existing_state_refuses_before_remote_checkpoint(self):
        (self.repo / "state" / "source.json").write_text("{}\n", encoding="utf-8")
        run(["git", "add", "state/source.json"], self.repo)
        run(["git", "commit", "--quiet", "-m", "existing state"], self.repo)
        run(["git", "push", "--quiet", "origin", "main"], self.repo)
        before = run(["git", "rev-parse", "refs/heads/main"], self.origin).stdout.strip()
        _, script = step_block("Acquire persistent run checkpoint")
        output = self.temp / "baseline-output"
        env = {**self.checkpoint_env(output), "CHECKPOINT_OPERATION": "baseline"}
        result = run(["bash", "-c", script], self.repo, env=env, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertFalse((self.repo / "state" / "_workflow_checkpoint.json").exists())
        self.assertEqual(before, run(["git", "rev-parse", "refs/heads/main"], self.origin).stdout.strip())

    def test_failed_final_push_leaves_upstream_checkpoint(self):
        self.acquire_checkpoint()
        (self.repo / "state" / "result.json").write_text("{}\n", encoding="utf-8")
        run(["git", "remote", "set-url", "origin", str(self.temp / "missing.git")], self.repo)
        fake_bin = self.temp / "bin"
        fake_bin.mkdir()
        sleep = fake_bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sleep.chmod(0o755)
        _, script = step_block("Persist only test state")
        result = run(
            ["bash", "-c", script], self.repo,
            env={"PATH": f"{fake_bin}:{os.environ['PATH']}"}, check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        clone = self.temp / "after-failure"
        run(["git", "clone", "--quiet", str(self.origin), str(clone)], self.temp)
        self.assertTrue((clone / "state" / "_workflow_checkpoint.json").is_file())
        self.assertFalse((clone / "state" / "result.json").exists())

    def test_request_gate_accepts_exact_request_and_safe_deletion_only(self):
        _, script = step_block("Validate operation and repository marker")
        request = self.repo / ".github" / "catalog-test-request.json"
        request.write_text('{"operation":"run"}\n', encoding="utf-8")
        run(["git", "add", str(request.relative_to(self.repo))], self.repo)
        before = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        run(["git", "commit", "--quiet", "-m", "request"], self.repo)
        sha = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        output = self.temp / "request-output"
        env = {
            "GITHUB_REPOSITORY": "example-owner/watchtower-test-runtime",
            "GITHUB_REF": "refs/heads/main", "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "push", "EVENT_BEFORE": before, "EVENT_SHA": sha,
            "REQUESTED_OPERATION": "", "GITHUB_OUTPUT": str(output),
        }
        run(["bash", "-c", script], self.repo, env=env)
        self.assertEqual(output.read_text(), "authorized=true\nvalue=run\n")
        request.unlink()
        run(["git", "add", "-u"], self.repo)
        before = sha
        run(["git", "commit", "--quiet", "-m", "delete request"], self.repo)
        sha = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        output.unlink()
        run(["bash", "-c", script], self.repo, env={**env, "EVENT_BEFORE": before, "EVENT_SHA": sha})
        self.assertEqual(output.read_text(), "authorized=false\n")

    def test_request_gate_rejects_mixed_commit_and_fresh_main_fast_forwards(self):
        _, gate = step_block("Validate operation and repository marker")
        request = self.repo / ".github" / "catalog-test-request.json"
        request.write_text('{"operation":"run"}\n', encoding="utf-8")
        (self.repo / "state" / "mixed.json").write_text("{}\n", encoding="utf-8")
        run(["git", "add", "."], self.repo)
        before = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        run(["git", "commit", "--quiet", "-m", "mixed"], self.repo)
        sha = run(["git", "rev-parse", "HEAD"], self.repo).stdout.strip()
        env = {
            "GITHUB_REPOSITORY": "example-owner/watchtower-test-runtime",
            "GITHUB_REF": "refs/heads/main", "REPOSITORY_PRIVATE": "true",
            "EVENT_NAME": "push", "EVENT_BEFORE": before, "EVENT_SHA": sha,
            "REQUESTED_OPERATION": "", "GITHUB_OUTPUT": str(self.temp / "mixed-output"),
        }
        self.assertNotEqual(run(["bash", "-c", gate], self.repo, env=env, check=False).returncode, 0)
        run(["git", "reset", "--hard", "HEAD~1"], self.repo)
        run(["git", "push", "--force", "origin", "main"], self.repo)
        queued = self.temp / "queued"
        run(["git", "clone", "--quiet", str(self.origin), str(queued)], self.temp)
        (self.repo / "state" / "new.json").write_text("{}\n", encoding="utf-8")
        run(["git", "add", "."], self.repo)
        run(["git", "commit", "--quiet", "-m", "new state"], self.repo)
        run(["git", "push", "--quiet", "origin", "main"], self.repo)
        _, fresh = step_block("Move to freshest main state after queueing")
        run(["bash", "-c", fresh], queued)
        self.assertTrue((queued / "state" / "new.json").is_file())


if __name__ == "__main__":
    unittest.main()
