from __future__ import annotations

import inspect

from creator_intel.cli import analyze_profile_command, prepare_codex_command
from creator_intel.codex_native import prepare_codex_profile
from creator_intel.pipeline import analyze_profile


def test_default_review_sample_is_ten_ads_plus_five_context_videos() -> None:
    cli_defaults = inspect.signature(prepare_codex_command).parameters
    api_defaults = inspect.signature(prepare_codex_profile).parameters
    assert cli_defaults["limit"].default == 100
    assert cli_defaults["top"].default == 10
    assert cli_defaults["context_videos"].default == 5
    assert api_defaults["limit"].default == 100
    assert api_defaults["top"].default == 10
    assert api_defaults["context_videos"].default == 5
    assert inspect.signature(analyze_profile_command).parameters["top"].default == 10
    assert inspect.signature(analyze_profile).parameters["top"].default == 10
