"""Local setup of an independent private runtime. Never creates credentials."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

from .config import load_config
from .engine import build_source
from .rss_profiles import load_profiles
from .runtime_safety import SECRET_PATTERNS
from .sources.identifiers import valid_orgnr


PRESETS = {
    "general": ("Generell næringslivsovervåking", ()),
    "finance": ("Finans", ("finanstilsynet", "norges_bank_pressemeldinger")),
    "health": ("Helse og legemidler", ("ema_news", "ema_human_medicines")),
}

# The two news feeds in the complete Finanstilsynet profile overlap. The starter
# pack uses one news feed plus circulars; owners can add the other explicitly.
STARTER_FEEDS = {
    "finanstilsynet": (
        "https://www.finanstilsynet.no/rss/nyhetsfeed/",
        "https://www.finanstilsynet.no/rss/rundskriv/",
    ),
}


def _quoted(value) -> str:
    # JSON strings/arrays are also valid TOML for the input values used here.
    return json.dumps(value, ensure_ascii=False)


def make_config(preset: str, topics: list[str], companies: list[str], provider: str) -> str:
    if preset not in PRESETS:
        raise ValueError("unknown setup preset")
    if provider not in {"teams", "slack"}:
        raise ValueError("notification provider must be teams or slack")
    topics = list(dict.fromkeys(term.strip() for term in topics if term.strip()))
    if not topics and not companies:
        raise ValueError("add at least one topic or company")
    lines = [
        "# Privat konfigurasjon. Nøkler og webhook-adresser hører hjemme i Actions Secrets.",
        "[general]", "config_version = 1", "max_seen_per_source = 3000", "",
        "[notifications]", f"provider = {_quoted(provider)}", "",
    ]
    entity_ids = []
    for company in companies:
        orgnr, separator, name = company.partition("=")
        orgnr, name = orgnr.strip().replace(" ", ""), name.strip()
        if not separator or not valid_orgnr(orgnr) or not name:
            raise ValueError("company must be ORGNR=NAME with a valid organisation number")
        entity_id = f"company_{orgnr}"
        if entity_id in entity_ids:
            raise ValueError("company organisation numbers must be unique")
        entity_ids.append(entity_id)
        lines.extend([
            "[[entity]]", f"id = {_quoted(entity_id)}", f"orgnr = {_quoted(orgnr)}",
            f"name = {_quoted(name)}", "aliases = []", "isins = []", "",
        ])

    def add_source(source_id, kind, label, options=(), *, register=False, updates=True):
        lines.extend([
            "[[source]]", f"id = {_quoted(source_id)}", f"kind = {_quoted(kind)}",
            f"label = {_quoted(label)}", "enabled = true", "interval_minutes = 60",
            f"alert_on_update = {str(updates).lower()}", *options,
            "[source.filter]",
        ])
        if register:
            lines.append("match_all = true")
        else:
            lines.extend([
                f"include_any = {_quoted(topics)}", f"entity_refs = {_quoted(entity_ids)}",
                'match_mode = "smart"',
            ])
        lines.extend(["exclude_any = []", ""])

    add_source("regjeringen", "regjeringen", "Regjeringen")
    add_source("stortinget", "stortinget", "Stortinget", updates=False)
    add_source("konkurransetilsynet", "konkurransetilsynet", "Konkurransetilsynet")
    if entity_ids:
        add_source("brreg", "brreg", "Brønnøysundregistrene", (
            f"entity_refs = {_quoted(entity_ids)}",
            'events = ["company", "roles", "annual_accounts"]',
        ), register=True)
        if preset == "finance":
            add_source("finanstilsynet_registry", "finanstilsynet_registry", "Finanstilsynets virksomhetsregister", (
                f"entity_refs = {_quoted(entity_ids)}", "max_pages = 3",
            ), register=True)
    profiles = {row["id"]: row for row in load_profiles()}
    for profile_id in PRESETS[preset][1]:
        profile = profiles[profile_id]
        feed_urls = STARTER_FEEDS.get(profile_id, profile["feed_urls"])
        if any(url not in profile["feed_urls"] for url in feed_urls):
            raise ValueError("starter feeds no longer match the public profile catalog")
        for index, url in enumerate(feed_urls, start=1):
            # A single failed feed cannot block another feed's state or alerts.
            add_source(f"{profile_id}_{index}", "rss", f"{profile['name']} ({index})", (
                f"urls = {_quoted([url])}", "allow_empty = false",
            ))
    content = "\n".join(lines)
    if any(pattern.search(content) for pattern in SECRET_PATTERNS):
        raise ValueError("setup input contains a credential; use Actions Secrets")
    return content


def write_runtime(root: Path, content: str) -> Path:
    root = root.expanduser().absolute()
    public_root = Path(__file__).resolve().parents[1]
    if root.resolve().is_relative_to(public_root):
        raise ValueError("choose a private runtime outside the public code directory")
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError("runtime path must not use symbolic links")
    config_dir, state_dir = root / "config", root / "state"
    target, backup = config_dir / "watchtower.toml", config_dir / "watchtower.before-setup.toml"
    for path in (config_dir, state_dir, target, backup):
        if path.is_symlink():
            raise ValueError("setup paths must not be symbolic links")
    if state_dir.exists() and any(path.name != ".gitkeep" for path in state_dir.iterdir()):
        raise ValueError("runtime already has state; edit its configuration instead of running setup")
    old_content = None
    if target.exists():
        old_content = target.read_text(encoding="utf-8")
        old_config = load_config(target)
        if any(source.enabled for source in old_config.sources) or backup.exists():
            raise ValueError("setup will not replace an active or previously configured runtime")
    # Validate completely before creating or replacing any runtime files.
    with tempfile.TemporaryDirectory() as temporary:
        candidate = Path(temporary) / "watchtower.toml"
        candidate.write_text(content, encoding="utf-8")
        config = load_config(candidate)
        for source in config.sources:
            if source.enabled:
                build_source(source)
    config_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(exist_ok=True)
    if old_content is not None:
        with backup.open("x", encoding="utf-8") as handle:
            handle.write(old_content)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=config_dir, delete=False) as handle:
        temporary_path = Path(handle.name)
        handle.write(content)
    try:
        temporary_path.replace(target)
    finally:
        temporary_path.unlink(missing_ok=True)
    (state_dir / ".gitkeep").touch(exist_ok=True)
    return target


def setup(args) -> int:
    preset, topics, companies, provider = args.preset, list(args.topic), list(args.company), args.channel
    if preset is None:
        print("Startpakker: general (generell), finance (finans), health (helse).")
        preset = input("Velg startpakke [general]: ").strip() or "general"
        if preset not in PRESETS:
            raise ValueError("velg general, finance eller health")
        if not topics:
            topics = [term.strip() for term in input("Temaer, adskilt med komma: ").split(",") if term.strip()]
        print("Legg til virksomheter som ORGNR=NAVN. Tom linje avslutter listen.")
        while company := input("Virksomhet: ").strip():
            companies.append(company)
        provider = input(f"Varslingskanal [{provider}]: ").strip() or provider
    content = make_config(preset, topics, companies, provider)
    target = write_runtime(Path(args.runtime), content)
    count = sum(source.enabled for source in load_config(target).sources)
    print(f"Oppsett laget: {target}; aktive kilder={count}")
    print("En eventuell tidligere, deaktivert konfigurasjon er bevart i config/watchtower.before-setup.toml.")
    print("Lagre konfigurasjonen i ditt private runtime-repo. Følg INSTALL.md for kobling og Secrets.")
    print("Test varsling, etabler stille baseline og kontroller en senere automatisk kjøring.")
    print("Startpakkene bruker åpne kilder og krever ingen ekstra kildenøkler.")
    return 0
