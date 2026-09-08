from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from watchtower.github_setup import GitHub, SetupError, connect, inspect_connection, link_github


class FakeGitHub:
    def __init__(self):
        self.variables = {}
        self.secrets = set()
        self.keys = []
        self.writes = []
        self.runtime_private = True
        self.fail_secret = False
        self.fail_variable = False

    def api(self, endpoint, method="GET", data=None):
        if method == "POST":
            self.writes.append(("create", data))
            self.keys.append({"id": 71, **data})
            return {"id": 71}
        if method == "DELETE":
            self.writes.append(("delete", endpoint))
            self.keys = [key for key in self.keys if key["id"] != int(endpoint.rsplit("/", 1)[1])]
            return None
        if endpoint == "repos/synthetic/code":
            return {"full_name": "synthetic/code", "private": False, "permissions": {"admin": True}}
        if endpoint == "repos/synthetic/runtime":
            return {"full_name": "synthetic/runtime", "private": self.runtime_private, "permissions": {"admin": True}}
        if "/contents/" in endpoint:
            return {"type": "file"}
        if "/actions/secrets" in endpoint:
            return {"total_count": len(self.secrets), "secrets": [{"name": name} for name in self.secrets]}
        if "/actions/variables" in endpoint:
            return {"total_count": len(self.variables), "variables": [{"name": key, "value": val} for key, val in self.variables.items()]}
        if endpoint.endswith("/keys?per_page=100"):
            return self.keys
        raise AssertionError(endpoint)

    def variable(self, repo, name, value):
        if self.fail_variable:
            raise SetupError("simulated variable failure")
        self.variables[name] = value
        self.writes.append(("variable", name))

    def secret(self, repo, name, value):
        if self.fail_secret:
            raise SetupError("simulated secret failure")
        self.secrets.add(name)
        self.writes.append(("secret", name))


def plan(gh):
    return inspect_connection(gh, "synthetic/code", "synthetic/runtime")


class GitHubSetupTests(unittest.TestCase):
    def test_preview_never_mutates(self):
        gh = FakeGitHub()
        args = Namespace(code_repo="synthetic/code", runtime_repo="synthetic/runtime", runtime_ref="main", apply=False)
        with patch("watchtower.github_setup.GitHub", return_value=gh), redirect_stdout(StringIO()):
            self.assertEqual(0, link_github(args))
        self.assertEqual([], gh.writes)

    def test_private_destination_and_existing_binding_are_checked_before_mutation(self):
        gh = FakeGitHub()
        for private, binding in ((False, None), (True, "synthetic/another-runtime")):
            gh.runtime_private = private
            gh.variables = {"WATCHTOWER_RUNTIME_REPOSITORY": binding} if binding else {}
            with self.assertRaises(SetupError):
                plan(gh)
            self.assertEqual([], gh.writes)

    def test_stale_preview_is_rechecked_and_cannot_change_destination(self):
        gh = FakeGitHub()
        preview = plan(gh)
        gh.variables["WATCHTOWER_RUNTIME_REPOSITORY"] = "synthetic/another-runtime"
        with self.assertRaises(SetupError):
            connect(gh, preview)
        self.assertEqual([], gh.writes)

    def test_success_is_repeatable_and_temporary_key_directory_is_removed(self):
        gh = FakeGitHub()
        directories = []
        def pair(directory):
            directories.append(directory)
            (directory / "synthetic-key").write_text("synthetic private material")
            return "synthetic private material", "synthetic public material"
        with patch("watchtower.github_setup._key_pair", side_effect=pair) as keys:
            connect(gh, plan(gh))
            connect(gh, plan(gh))
        self.assertEqual(1, keys.call_count)
        self.assertTrue(all(not path.exists() for path in directories))
        self.assertEqual(["variable", "variable", "create", "secret"], [row[0] for row in gh.writes[:4]])
        self.assertEqual("synthetic/runtime", gh.variables["WATCHTOWER_RUNTIME_REPOSITORY"])
        self.assertFalse(gh.keys[0]["read_only"])

    def test_failed_variable_write_does_not_create_keys(self):
        gh = FakeGitHub()
        gh.fail_variable = True
        with self.assertRaises(SetupError):
            connect(gh, plan(gh))
        self.assertEqual([], gh.keys)
        self.assertEqual(set(), gh.secrets)

    def test_failed_secret_write_removes_only_new_deploy_key(self):
        gh = FakeGitHub()
        gh.keys = [{"id": 19, "read_only": False}]
        gh.fail_secret = True
        with patch("watchtower.github_setup._key_pair", return_value=("private synthetic", "public synthetic")):
            with self.assertRaisesRegex(SetupError, "ikke bekreftet"):
                connect(gh, plan(gh))
        self.assertEqual([{"id": 19, "read_only": False}], gh.keys)
        self.assertEqual(("delete", "repos/synthetic/runtime/keys/71"), gh.writes[-1])

    def test_existing_secret_without_writable_runtime_key_is_not_reported_as_connected(self):
        gh = FakeGitHub()
        gh.secrets.add("RUNTIME_DEPLOY_KEY")
        gh.variables["WATCHTOWER_RUNTIME_REPOSITORY"] = "synthetic/runtime"
        with self.assertRaisesRegex(SetupError, "skrivbar"):
            plan(gh)

    def test_secret_uses_stdin_and_failure_output_never_echoes_payload(self):
        gh = GitHub()
        with patch("watchtower.github_setup.subprocess.run", return_value=Mock(returncode=1, stdout="synthetic secret", stderr="synthetic secret")) as command:
            with self.assertRaises(SetupError) as error:
                gh.secret("synthetic/code", "RUNTIME_DEPLOY_KEY", "synthetic secret")
        self.assertNotIn("synthetic secret", str(error.exception))
        self.assertNotIn("synthetic secret", command.call_args.args[0])
        self.assertEqual("synthetic secret", command.call_args.kwargs["input"])
        self.assertTrue(command.call_args.kwargs["capture_output"])

    def test_timeout_and_invalid_repo_names_fail_safely(self):
        with patch("watchtower.github_setup.subprocess.run", side_effect=subprocess.TimeoutExpired("gh", 60)):
            with self.assertRaises(SetupError):
                GitHub().command(["api", "repos/synthetic/code"])
        for name in ("https://github.com/synthetic/code", "--repo", "synthetic/code;command"):
            with self.subTest(name=name), self.assertRaises(SetupError):
                inspect_connection(FakeGitHub(), name, "synthetic/runtime")


if __name__ == "__main__":
    unittest.main()
