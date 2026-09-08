"""Connect existing repos using the owner's authenticated GitHub CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


class SetupError(RuntimeError):
    pass


class GitHub:
    def command(self, args: list[str], *, body: str | None = None) -> str:
        env = {**os.environ, "GH_HOST": "github.com", "GH_PROMPT_DISABLED": "1"}
        try:
            result = subprocess.run(["gh", *args], input=body, capture_output=True,
                                    text=True, timeout=60, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise SetupError("GitHub CLI kunne ikke kjøres. Installer gh og kjør gh auth login.") from None
        if result.returncode:
            # gh output can contain submitted payloads. Never echo it or argv.
            raise SetupError("GitHub avviste et oppsettssteg. Kontroller innlogging og administratortilgang.")
        return result.stdout

    def api(self, endpoint: str, *, method: str = "GET", data=None):
        args = ["api", "--hostname", "github.com", endpoint, "--method", method]
        if data is not None:
            args.extend(["--input", "-"])
        output = self.command(args, body=json.dumps(data) if data is not None else None)
        return json.loads(output) if output.strip() else None

    def secret(self, repo: str, name: str, value: str):
        self.command(["secret", "set", name, "--repo", repo, "--app", "actions"], body=value)

    def variable(self, repo: str, name: str, value: str):
        self.command(["variable", "set", name, "--repo", repo], body=value)


def _repo_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise SetupError("Oppgi repo som EIER/NAVN på github.com.")
    return value


def inspect_connection(gh: GitHub, code_repo: str, runtime_repo: str, ref: str = "main") -> dict:
    code_repo, runtime_repo = _repo_name(code_repo), _repo_name(runtime_repo)
    if code_repo.casefold() == runtime_repo.casefold():
        raise SetupError("Kode og privat runtime må være ulike repoer.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]*", ref) or ".." in ref:
        raise SetupError("Ugyldig runtime-branch.")
    for repo, private in ((code_repo, False), (runtime_repo, True)):
        metadata = gh.api(f"repos/{repo}")
        if metadata.get("full_name", "").casefold() != repo.casefold() or metadata.get("private") is not private:
            raise SetupError("Repoene må være en offentlig kodefork og en privat runtime.")
        if metadata.get("permissions", {}).get("admin") is not True:
            raise SetupError("Du trenger administratortilgang til begge repoene for nøkkeloppsett.")
    # Ensure the selected repo/ref actually has the expected runtime config.
    gh.api(f"repos/{runtime_repo}/contents/config/watchtower.toml?ref={ref}")
    gh.api(f"repos/{code_repo}/contents/.github/workflows/monitor.yml")
    secrets = gh.api(f"repos/{code_repo}/actions/secrets?per_page=100")
    variables = gh.api(f"repos/{code_repo}/actions/variables?per_page=100")
    if secrets.get("total_count", 0) > 100 or variables.get("total_count", 0) > 100:
        raise SetupError("Oppsettet krever gjennomgang av repoets mange secrets/variabler.")
    names = {row["name"] for row in secrets.get("secrets", [])}
    values = {row["name"]: row["value"] for row in variables.get("variables", [])}
    old_repo = values.get("WATCHTOWER_RUNTIME_REPOSITORY")
    old_ref = values.get("WATCHTOWER_RUNTIME_REF")
    if old_repo and old_repo.casefold() != runtime_repo.casefold():
        raise SetupError("Kodeforken er allerede koblet til en annen runtime; koblingen endres ikke.")
    if old_ref and old_ref != ref:
        raise SetupError("Kodeforken bruker allerede en annen runtime-branch; koblingen endres ikke.")
    if "RUNTIME_DEPLOY_KEY" in names and not old_repo:
        default = code_repo.split("/", 1)[0] + "/watchtower-runtime"
        if runtime_repo.casefold() != default.casefold():
            raise SetupError("Eksisterende nøkkel har ukjent runtime-kobling; avklar denne først.")
    if "RUNTIME_DEPLOY_KEY" in names:
        keys = gh.api(f"repos/{runtime_repo}/keys?per_page=100")
        if not isinstance(keys, list) or not any(key.get("read_only") is False for key in keys):
            raise SetupError("Runtime mangler en skrivbar deploy-nøkkel. Kontroller koblingen og fjern bare en bekreftet ubrukt RUNTIME_DEPLOY_KEY-secret før nytt oppsett.")
    return {"code": code_repo, "runtime": runtime_repo, "ref": ref, "has_key": "RUNTIME_DEPLOY_KEY" in names}


def _key_pair(directory: Path) -> tuple[str, str]:
    path = directory / "runtime-key"
    try:
        result = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)],
                                capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError("Kunne ikke lage nøkkel. Installer OpenSSH/ssh-keygen.") from None
    if result.returncode:
        raise SetupError("Kunne ikke lage en egen runtime-nøkkel.")
    return path.read_text(), path.with_suffix(".pub").read_text().strip()


def connect(gh: GitHub, plan: dict) -> None:
    # Recheck before mutation so a stale preview cannot rewire an installation.
    plan = inspect_connection(gh, plan["code"], plan["runtime"], plan["ref"])
    # Write the non-secret binding first. A retry after an interrupted setup can
    # then recognize the intended destination without replacing existing keys.
    gh.variable(plan["code"], "WATCHTOWER_RUNTIME_REPOSITORY", plan["runtime"])
    gh.variable(plan["code"], "WATCHTOWER_RUNTIME_REF", plan["ref"])
    if not plan["has_key"]:
        with tempfile.TemporaryDirectory(prefix="watchtower-key-") as directory:
            private, public = _key_pair(Path(directory))
            created = gh.api(f"repos/{plan['runtime']}/keys", method="POST", data={
                "title": f"Watchtower: {plan['code']}", "key": public, "read_only": False,
            })
            key_id = created.get("id")
            if isinstance(key_id, bool) or not isinstance(key_id, int):
                raise SetupError("GitHub bekreftet ikke nøkkel-ID. Kontroller runtime-repoets deploy keys.")
            try:
                gh.secret(plan["code"], "RUNTIME_DEPLOY_KEY", private)
            except Exception:
                try:
                    gh.api(f"repos/{plan['runtime']}/keys/{key_id}", method="DELETE")
                except Exception:
                    raise SetupError("Nøkkeloppsett feilet; fjern den nye Watchtower-nøkkelen i runtime-repoets Deploy keys.") from None
                raise SetupError("Secret-lagring ble ikke bekreftet. Den nye deploy-nøkkelen er fjernet; eksisterende nøkler er beholdt. Kontroller om GitHub likevel lagret en ny RUNTIME_DEPLOY_KEY-secret, og fjern i så fall denne før nytt forsøk.") from None


def link_github(args) -> int:
    gh = GitHub()
    try:
        plan = inspect_connection(gh, args.code_repo, args.runtime_repo, args.runtime_ref)
        print(f"Offentlig kode: {plan['code']}\nPrivat runtime: {plan['runtime']} ({plan['ref']})")
        print("Nøkkel: behold eksisterende" if plan["has_key"] else "Nøkkel: opprett egen deploy-nøkkel og Actions-secret")
        if not args.apply:
            print("Kontroll fullført. Legg til --apply for å lagre denne koblingen.")
            return 0
        connect(gh, plan)
        print("Runtime-kobling lagret. Fortsett med kanal-secret og test-notification i INSTALL.md.")
        return 0
    except (SetupError, ValueError) as exc:
        print(str(exc) if isinstance(exc, SetupError) else "GitHub returnerte et uventet svar.")
        return 1
