from pathlib import Path

from pydantic import BaseModel
from textual_fspicker import FileOpen, SelectDirectory

from clabe.ui import FormRequest
from clabe.ui._textual_form import _FormScreen, build_picker_screen


class _Optional(BaseModel):
    count: int | None = None


def test_form_treats_a_pep_604_optional_field_as_optional():
    screen = _FormScreen(FormRequest(model=_Optional))
    errors = []
    screen._clear_field_error = lambda field_name: None  # not mounted: no error labels to update
    screen._show_field_error = lambda field_name, message: errors.append(message)
    assert screen._validate_field("count", "")  # empty means None, not an invalid int
    assert errors == []


class TestBuildPickerScreen:
    """`build_picker_screen` is a pure factory — no running app needed."""

    def test_kind_dir_returns_select_directory(self, tmp_path):
        screen = build_picker_screen(label="Pick a rig folder", start=tmp_path, kind="dir")
        assert isinstance(screen, SelectDirectory)

    def test_kind_file_returns_file_open(self, tmp_path):
        screen = build_picker_screen(label="Pick a rig file", start=tmp_path, kind="file")
        assert isinstance(screen, FileOpen)

    def test_defaults_to_home_when_no_start_given(self):
        screen = build_picker_screen(label="Pick", start=None, kind="file")
        assert screen._location == Path.home()

    def test_nonexistent_start_resolves_to_nearest_existing_ancestor(self, tmp_path):
        # textual-fspicker lists `location` unconditionally and crashes (FileNotFoundError)
        # if it doesn't exist — a real crash hit when start pointed at an output dir that
        # hadn't been created yet. The nearest existing ancestor must be used instead, with
        # the missing leaf preserved as a suggested filename rather than silently dropped.
        missing = tmp_path / "not_created_yet" / "rig.json"
        screen = build_picker_screen(label="Pick", start=missing, kind="file")
        assert screen._location == tmp_path
        assert screen._default_file == "rig.json"

    def test_nonexistent_start_for_dir_kind_also_resolves_safely(self, tmp_path):
        missing = tmp_path / "not_created_yet"
        screen = build_picker_screen(label="Pick", start=missing, kind="dir")
        assert isinstance(screen, SelectDirectory)
        assert screen._location == tmp_path

    def test_existing_start_has_no_default_file(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file")
        assert screen._location == tmp_path
        assert screen._default_file is None

    def test_extensions_filter_matches_allowed_suffix_only(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file", extensions=[".json"])
        assert screen._filters is not None
        allowed_filter = screen._filters[0]
        assert allowed_filter(Path("rig.json")) is True
        assert allowed_filter(Path("notes.txt")) is False

    def test_extensions_normalizes_missing_leading_dot(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file", extensions=["json"])
        allowed_filter = screen._filters[0]
        assert allowed_filter(Path("rig.json")) is True

    def test_extensions_matching_is_case_insensitive_on_the_file(self, tmp_path):
        # Regression: the picker used to lower-case only the candidate's suffix, so a
        # real Rig1.JSON file would be offered here but then rejected by
        # FrontendBase._validate_path (which didn't lower-case at all) -- an
        # unescapable reprompt loop for a file the picker itself just showed.
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file", extensions=[".json"])
        allowed_filter = screen._filters[0]
        assert allowed_filter(Path("Rig1.JSON")) is True

    def test_extensions_matching_is_case_insensitive_on_the_request(self, tmp_path):
        # Regression: the configured extension itself wasn't lower-cased, so
        # extensions=[".JSON"] matched nothing, even an exact-case "x.JSON" file.
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file", extensions=[".JSON"])
        allowed_filter = screen._filters[0]
        assert allowed_filter(Path("rig.json")) is True

    def test_no_extensions_means_no_filter(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file")
        assert screen._filters is None

    def test_extensions_ignored_for_dir_kind(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="dir", extensions=[".json"])
        assert isinstance(screen, SelectDirectory)

    def test_must_exist_forwarded_to_file_open(self, tmp_path):
        screen = build_picker_screen(label="Pick", start=tmp_path, kind="file", must_exist=False)
        assert screen._must_exist is False
