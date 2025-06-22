import unittest
from datetime import datetime
from unittest.mock import MagicMock, create_autospec, patch

from aind_behavior_services import AindBehaviorRigModel, AindBehaviorSessionModel, AindBehaviorTaskLogicModel
from aind_slims_api.exceptions import SlimsRecordNotFound
from aind_slims_api.models import SlimsAttachment, SlimsBehaviorSession, SlimsInstrument, SlimsMouseContent
from pydantic import ValidationError

from clabe.behavior_launcher import (
    BehaviorLauncher,
    BehaviorServicesFactoryManager,
    SlimsPicker,
)
from clabe.behavior_launcher._launcher import ByAnimalFiles
from clabe.launcher.cli import BaseCliArgs

MOCK_MOUSE = SlimsMouseContent(point_of_contact="test", water_restricted=False, barcode="test", baseline_weight_g=0)
MOCK_TASK_LOGIC_ATTACHMENT = SlimsAttachment(pk=0, name=ByAnimalFiles.TASK_LOGIC.value)


def slims_validation_error() -> ValidationError:
    """
    Convenience function to easily return a pydantic ValidationError
    """
    try:
        AindBehaviorRigModel()
    except Exception as e:
        return e


class TestSlimsPicker(unittest.TestCase):
    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        __init__=MagicMock(return_value=None),
        fetch_model=MagicMock(return_value=None),
    )
    def setUp(self):
        self.services_factory_manager = create_autospec(BehaviorServicesFactoryManager)

        self.launcher = BehaviorLauncher(
            rig_schema_model=MagicMock(),
            task_logic_schema_model=MagicMock(),
            session_schema_model=MagicMock(),
            services=self.services_factory_manager,
            settings=BaseCliArgs(
                data_dir="/path/to/data",
                temp_dir="/path/to/temp",
                repository_dir=None,
                allow_dirty=False,
                skip_hardware_validation=False,
                debug_mode=False,
                group_by_subject_log=False,
                validate_init=False,
            ),
            attached_logger=None,
            picker=SlimsPicker(
                username="test",
                password="test",
                ui_helper=MagicMock(),
            ),
        )
        self.picker = self.launcher.picker

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_model=MagicMock(side_effect=[SlimsRecordNotFound(), SlimsInstrument(name="test")]),
        fetch_attachments=MagicMock(return_value=[SlimsAttachment(pk=0, name="namesake")] * 2),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_rig_record_not_found(self):
        # test no rig found on slims
        rig = self.picker.pick_rig()
        # fetch_model was called twice (once for failure, once for success)
        self.assertEqual(self.picker.slims_client.fetch_model.call_count, 2)
        self.assertIsNotNone(rig)
        self.assertIsNotNone(self.picker.slims_rig)  # check that rig has been saved as property

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_model=MagicMock(return_value=SlimsInstrument(name="test")),
        fetch_attachments=MagicMock(return_value=[SlimsAttachment(pk=0, name="namesake")] * 2),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_rig_invalid_json(self):
        # test invalid json
        self.launcher.rig_schema_model.side_effect = [slims_validation_error(), MagicMock()]
        self.picker.pick_rig()
        # model validated twice more (once for failure, once for success)
        self.assertEqual(self.picker.launcher.rig_schema_model.call_count, 2)

        # test all invalid jsons
        self.picker.slims_client.fetch_model.side_effect = SlimsInstrument(name="test")
        self.launcher.rig_schema_model.side_effect = [slims_validation_error(), slims_validation_error()]
        with self.assertRaises(ValueError) as context:
            self.picker.pick_rig()
            self.assertTrue("No rig configuration found attached to rig model" in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_model=MagicMock(return_value=SlimsInstrument(name="test")),
        fetch_attachments=MagicMock(return_value=[]),
    )
    def test_pick_rig_no_attachments(self):
        # test no attachments
        with self.assertRaises(ValueError) as context:
            self.picker.pick_rig()
            self.assertTrue("No valid rig configuration found attached to rig model" in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_model=MagicMock(side_effect=[SlimsRecordNotFound(), MOCK_MOUSE]),
        fetch_models=MagicMock(side_effect=[[SlimsBehaviorSession()], []]),
    )
    def test_pick_session(self):
        # test mouse not found
        self.picker.ui_helper.prompt_text.return_value = "Chris P. Bacon"
        session = self.picker.pick_session()
        # fetch_model was called twice (once for failure, once for success)
        self.assertEqual(self.picker.slims_client.fetch_model.call_count, 2)
        self.assertIsNotNone(session)
        self.assertIsNotNone(self.picker.slims_session)  # check that session has been saved as property
        self.assertIsNotNone(self.picker.slims_mouse)  # check that mouse has been saved as property

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_model=MagicMock(return_value=MOCK_MOUSE),
        fetch_models=MagicMock(return_value=[]),
    )
    def test_pick_session_no_sessions(self):
        self.picker.ui_helper.prompt_text.return_value = "Chris P. Bacon"
        # test no sessions on slims
        with self.assertRaises(ValueError) as context:
            self.picker.pick_session()
            self.assertTrue("No session found on slims for mouse" in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_attachments=MagicMock(return_value=[MOCK_TASK_LOGIC_ATTACHMENT]),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_task_logic(self):
        # test success
        self.picker._slims_session = SlimsBehaviorSession()
        task_logic = self.picker.pick_task_logic()
        self.assertIsNotNone(task_logic)

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_attachments=MagicMock(return_value=[]),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_task_logic_no_attachments(self):
        # test no attachments
        self.picker._slims_session = SlimsBehaviorSession()
        with self.assertRaises(ValueError) as context:
            self.picker.pick_task_logic()
            self.assertTrue("No task_logic model found on with loaded slims session for " in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_attachments=MagicMock(return_value=[SlimsAttachment(pk=0, name="test")]),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_task_logic_incorrectly_named_attachment(self):
        # test incorrectly named attachment
        self.picker._slims_session = SlimsBehaviorSession()
        with self.assertRaises(ValueError) as context:
            self.picker.pick_task_logic()
            self.assertTrue("No task_logic model found on with loaded slims session for " in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        fetch_attachments=MagicMock(return_value=[MOCK_TASK_LOGIC_ATTACHMENT]),
        fetch_attachment_content=MagicMock(),
    )
    def test_pick_task_logic_no_session(self):
        # test no session picked
        self.picker._slims_session = None
        with self.assertRaises(ValueError) as context:
            self.picker.pick_task_logic()
            self.assertTrue("Slims session instance not set." in str(context.exception))

    @patch.multiple(
        "clabe.behavior_launcher.slims_picker.SlimsClient",
        add_model=MagicMock(return_value=SlimsBehaviorSession()),
        add_attachment_content=MagicMock(),
    )
    @patch.multiple(
        "clabe.behavior_launcher.BehaviorLauncher",
        session_schema=AindBehaviorSessionModel(experiment="", experiment_version="0.0.0", root_path="", subject="0"),
    )
    def test_push_session(self):
        self.picker._slims_mouse = MOCK_MOUSE
        self.picker._slims_rig = SlimsInstrument(name="test")
        task_logic = AindBehaviorTaskLogicModel(name="test", task_parameters={}, version="0.0.0")

        # test successful push with only required args
        self.picker.write_behavior_session(task_logic=task_logic)

        # test all args
        self.picker.write_behavior_session(
            task_logic=task_logic,
            notes="BlahBlahBlah",
            is_curriculum_suggestion=True,
            software_version="0.0.0",
            schedule_date=datetime(2025, 4, 23),
        )


class SlimsWaterCalculation(unittest.TestCase):
    def test_calculated_suggested_water(self):
        result = SlimsPicker._calculate_suggested_water(weight_g=20.0, water_earned_ml=1.0, baseline_weight_g=25)
        self.assertEqual(result, 5.0)

    def test_calculated_suggested_water_minimum(self):
        result = SlimsPicker._calculate_suggested_water(
            weight_g=20.0, water_earned_ml=1.0, baseline_weight_g=18, minimum_daily_water=2.0
        )
        self.assertEqual(result, 1.0)


if __name__ == "__main__":
    unittest.main()
