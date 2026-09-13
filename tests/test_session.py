import os

import pytest
from aind_behavior_services import Session

from clabe import ui
from clabe.cache_manager import CacheManager
from clabe.session import SessionBuilder

COMMIT = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def launcher(mock_base_launcher, mock_frontend):
    ui.set_current_frontend(mock_frontend)
    mock_base_launcher.repository.head.commit.hexsha = COMMIT
    return mock_base_launcher


@pytest.fixture
def builder(launcher):
    return SessionBuilder(launcher, experimenter_validator=None)


def answer(mock_frontend, *, autocomplete, notes=""):
    responses = iter(autocomplete)
    mock_frontend._ask_autocomplete_mock.side_effect = lambda _: next(responses)
    mock_frontend._ask_text_mock.return_value = notes


class TestSessionBuilder:
    def test_build_collects_experimenter_subject_and_notes(self, builder, mock_frontend):
        answer(mock_frontend, autocomplete=["j.doe", "789012"], notes="all good")
        session = builder.build()

        assert isinstance(session, Session)
        assert session.experimenter == ["j.doe"]
        assert session.subject == "789012"
        assert session.notes == "all good"
        assert session.commit_hash == COMMIT

    def test_experimenters_may_be_comma_or_space_separated(self, builder, mock_frontend):
        answer(mock_frontend, autocomplete=["j.doe, a.smith b.jones"])
        assert builder.prompt_experimenter() == ["j.doe", "a.smith", "b.jones"]

    def test_an_empty_experimenter_is_rejected_and_reprompted(self, builder, mock_frontend):
        answer(mock_frontend, autocomplete=["", "j.doe"])
        assert builder.prompt_experimenter() == ["j.doe"]

    def test_an_invalid_experimenter_is_rejected_and_reprompted(self, launcher, mock_frontend):
        builder = SessionBuilder(launcher, experimenter_validator=lambda name: name == "j.doe")
        answer(mock_frontend, autocomplete=["nobody", "j.doe"])
        assert builder.prompt_experimenter() == ["j.doe"]

    def test_an_empty_subject_is_rejected_and_reprompted(self, builder, mock_frontend):
        answer(mock_frontend, autocomplete=["", "789012"])
        assert builder.choose_subject() == "789012"

    def test_choices_are_cached_for_the_next_run(self, builder, mock_frontend):
        answer(mock_frontend, autocomplete=["j.doe", "789012"])
        builder.build()

        cache = CacheManager.get_instance()
        assert cache.get_cache("subjects") == ["789012"]
        assert cache.get_cache("experimenters") == ["j.doe"]

    def test_cached_choices_seed_the_prompts(self, builder, mock_frontend):
        CacheManager.get_instance().add_to_cache("subjects", "789012")
        answer(mock_frontend, autocomplete=["j.doe", "789012"])
        builder.build()

        subject_request = mock_frontend._ask_autocomplete_mock.call_args.args[0]
        assert subject_request.options == ["789012"]

    def test_caching_can_be_turned_off(self, launcher, mock_frontend):
        CacheManager.get_instance().add_to_cache("subjects", "789012")
        builder = SessionBuilder(launcher, experimenter_validator=None, use_cache=False)
        answer(mock_frontend, autocomplete=["j.doe", "111111"])
        builder.build()

        subject_request = mock_frontend._ask_autocomplete_mock.call_args.args[0]
        assert subject_request.options == []

    def test_building_creates_no_subject_directory(self, builder, mock_frontend, monkeypatch):
        """Creating the subject folder is the store's job, on first write."""
        answer(mock_frontend, autocomplete=["j.doe", "789012"])
        monkeypatch.setattr(os, "makedirs", lambda *a, **k: pytest.fail("makedirs must not be called"))
        builder.build()
