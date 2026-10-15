"""The roster is a policy, so it is tested like one.

A role model that cannot REFUSE anything is decoration — it renames agents
without changing what any of them is allowed to conclude. So every test here
comes in a pair: something the policy permits, and something it denies. A
permission test alone would pass against a registry that said yes to everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cartograph.config import DEFAULT_EXCLUDE, EXAMPLE, ProjectConfig, load
from cartograph.roles import (
    CAPABILITIES,
    DEFAULT_ROSTER,
    Role,
    RoleRegistry,
    SeparationOfDutiesError,
)


# --------------------------------------------------------------------------
# Capabilities: must_not outranks may, and both are checkable.
# --------------------------------------------------------------------------

def test_must_not_outranks_may():
    """A withheld capability stays withheld even if also granted."""
    with pytest.raises(ValueError, match="both may and must_not"):
        Role(name="confused", purpose="x", may=("build",), must_not=("build",))


def test_a_role_refuses_a_capability_it_does_not_hold():
    reg = RoleRegistry.default()

    # Permitted.
    reg.assert_can("engineer", "build")
    reg.assert_can("foreman", "merge")

    # Denied — and the denial names who DOES hold it, so it routes rather than
    # just blocking.
    with pytest.raises(SeparationOfDutiesError) as exc:
        reg.assert_can("engineer", "merge")
    assert "foreman" in str(exc.value)
    assert "Escalate to reviewer" in str(exc.value)


def test_socrates_can_neither_approve_nor_block():
    """The seat exists to ask. If it could rule, it would become a gate."""
    s = RoleRegistry.default().get("socrates")
    assert s.can("question")
    for cap in ("build", "review", "merge", "deploy", "decide"):
        assert not s.can(cap), f"socrates must not {cap}"


def test_reviewer_cannot_build_what_it_reviews():
    r = RoleRegistry.default().get("reviewer")
    assert r.can("review") and not r.can("build")


def test_an_unknown_capability_is_refused_at_construction():
    """A typo'd capability would silently grant nothing and read as a grant."""
    with pytest.raises(ValueError, match="unknown capability"):
        Role(name="x", purpose="y", may=("deploy_to_prod",))


# --------------------------------------------------------------------------
# The invariant no single agent can check about itself.
# --------------------------------------------------------------------------

def test_one_role_raising_and_resolving_the_same_subject_is_flagged():
    reg = RoleRegistry.default()
    violations = reg.check_separation([
        ("reviewer", "review", "PR-12"),
        ("reviewer", "merge", "PR-12"),     # same seat closes its own finding
    ])
    assert len(violations) == 1
    assert "reviewer" in violations[0] and "PR-12" in violations[0]
    assert "cannot be caught getting it wrong" in violations[0]


def test_two_different_roles_on_one_subject_is_clean():
    """THE CONTROL. Without it, a checker that flagged everything would pass."""
    reg = RoleRegistry.default()
    assert reg.check_separation([
        ("reviewer", "review", "PR-12"),
        ("foreman", "merge", "PR-12"),      # a different seat resolves
    ]) == []


def test_the_same_role_on_two_different_subjects_is_clean():
    """Separation is per-subject. A reviewer reviewing many things is normal."""
    reg = RoleRegistry.default()
    assert reg.check_separation([
        ("reviewer", "review", "PR-1"),
        ("foreman", "merge", "PR-1"),
        ("reviewer", "review", "PR-2"),
        ("foreman", "merge", "PR-2"),
    ]) == []


def test_questioning_then_building_the_same_subject_is_flagged():
    """Raising covers `question`, not only `review`."""
    reg = RoleRegistry.default()
    v = reg.check_separation([
        ("socrates", "question", "expiry-booker"),
        ("socrates", "build", "expiry-booker"),
    ])
    assert len(v) == 1 and "socrates" in v[0]


# --------------------------------------------------------------------------
# The escalation graph must terminate.
# --------------------------------------------------------------------------

def test_escalation_cycle_is_refused():
    """A loop means no seat ends the argument."""
    reg = RoleRegistry(roles={
        "a": Role(name="a", purpose="", escalates_to="b"),
        "b": Role(name="b", purpose="", escalates_to="a"),
    })
    with pytest.raises(ValueError, match="cycle"):
        reg.validate()


def test_escalating_to_a_missing_role_is_refused():
    reg = RoleRegistry(roles={"a": Role(name="a", purpose="", escalates_to="ghost")})
    with pytest.raises(ValueError, match="not in the roster"):
        reg.validate()


def test_the_default_roster_terminates():
    RoleRegistry.default().validate()          # must not raise
    assert RoleRegistry.default().get("operator").escalates_to is None


def test_every_default_role_declares_a_purpose():
    for r in DEFAULT_ROSTER:
        assert r.purpose.strip(), f"{r.name} has no purpose"


def test_preamble_states_what_is_withheld():
    """The agent must be told what it may NOT do, not only what it may."""
    p = RoleRegistry.default().get("engineer").preamble()
    assert "MUST NOT" in p and "merge" in p
    assert "hand to: reviewer" in p


# --------------------------------------------------------------------------
# Config: nothing about a mapped repo is baked into the tool.
# --------------------------------------------------------------------------

def test_absent_config_yields_usable_defaults(tmp_path: Path):
    cfg = load(tmp_path)
    assert isinstance(cfg, ProjectConfig)
    assert cfg.exclude == DEFAULT_EXCLUDE
    assert cfg.language == "auto"
    assert not cfg.configured
    assert cfg.roles.get("foreman").can("merge")


def test_a_config_replaces_the_roster_rather_than_merging(tmp_path: Path):
    """A partial merge would restore capabilities someone thought they removed."""
    (tmp_path / "cartograph.toml").write_text(
        '[roles.only]\npurpose = "the sole seat"\nmay = ["question"]\n', encoding="utf-8")
    cfg = load(tmp_path)
    assert set(cfg.roles.roles) == {"only"}, "a default role survived a custom roster"
    with pytest.raises(KeyError, match="no role 'foreman'"):
        cfg.roles.get("foreman")


def test_a_malformed_config_raises_rather_than_silently_defaulting(tmp_path: Path):
    """The failure this prevents: you revoke a capability, the file fails to
    parse, defaults load, and the tool behaves as if your edit said nothing."""
    (tmp_path / "cartograph.toml").write_text("[project\nlanguage =", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid TOML"):
        load(tmp_path)


def test_config_reads_view_and_channel_settings(tmp_path: Path):
    (tmp_path / "cartograph.toml").write_text(
        '[project]\nexclude = ["vendor"]\n'
        '[view]\nresolution = 1.4\nmin_members = 5\n'
        '[channels.production]\ndescription = "what runs"\nentry_points = ["cron/"]\n',
        encoding="utf-8")
    cfg = load(tmp_path)
    assert cfg.exclude == ("vendor",)
    assert cfg.resolution == 1.4 and cfg.min_members == 5
    assert len(cfg.channels) == 1
    assert cfg.channels[0].name == "production"
    assert cfg.channels[0].entry_points == ("cron/",)


def test_the_shipped_example_config_actually_parses(tmp_path: Path):
    """An example that does not load is documentation that lies."""
    (tmp_path / "cartograph.toml").write_text(EXAMPLE, encoding="utf-8")
    cfg = load(tmp_path)
    cfg.roles.validate()
    assert "socrates" in cfg.roles.roles and "operator" in cfg.roles.roles
    assert not cfg.roles.get("socrates").can("merge")
    assert cfg.channels and cfg.channels[0].name == "production"


# --------------------------------------------------------------------------
# The language seam.
# --------------------------------------------------------------------------

def test_python_is_detected_and_a_bare_directory_is_refused(tmp_path: Path):
    from cartograph import languages

    (tmp_path / "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    det = languages.detect(tmp_path)
    assert det.adapter.name == "python" and det.confidence > 0

    # CONTROL: nothing recognisable must RAISE, not return an empty graph that
    # renders as "a codebase with no structure".
    empty = tmp_path / "docs"
    empty.mkdir()
    (empty / "notes.md").write_text("# hi\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no registered language adapter"):
        languages.detect(empty)


def test_a_mixed_repo_says_so(tmp_path: Path):
    from cartograph import languages

    for i in range(8):
        (tmp_path / f"m{i}.py").write_text("x = 1\n", encoding="utf-8")
    for i in range(4):
        (tmp_path / f"c{i}.ts").write_text("export const x = 1;\n", encoding="utf-8")

    det = languages.detect(tmp_path)
    assert det.adapter.name == "python"
    assert det.confidence < 1.0
    assert det.is_mixed, "a third of the repo is unmapped and the map did not say so"


def test_the_adapter_satisfies_the_protocol():
    from cartograph.languages import LanguageAdapter, PythonAdapter

    assert isinstance(PythonAdapter(), LanguageAdapter)


def test_capabilities_vocabulary_is_closed():
    """Every default role uses only the declared vocabulary."""
    for r in DEFAULT_ROSTER:
        for cap in (*r.may, *r.must_not):
            assert cap in CAPABILITIES


def test_withheld_and_unheld_are_different_claims():
    """Regression: the UI rendered "not granted" as "must not", which showed
    the human operator as forbidden from building. A policy display that turns
    silence into prohibition is asserting something nobody wrote."""
    reg = RoleRegistry.default()

    op = reg.get("operator")
    assert op.must_not == (), "the operator seat forbids nothing"
    assert op.can("build"), "the human may build"

    eng = reg.get("engineer")
    assert "merge" in eng.must_not, "engineer is EXPLICITLY withheld merge"
    assert not eng.can("deploy")
    assert "deploy" not in eng.must_not, (
        "deploy is merely not granted to engineer, not explicitly forbidden — "
        "the two must stay distinguishable"
    )
