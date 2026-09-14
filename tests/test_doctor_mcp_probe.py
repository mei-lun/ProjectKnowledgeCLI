import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from project_knowledge.cli import main


class DoctorMCPProbeTests(unittest.TestCase):
    def test_doctor_probe_failure_is_nonzero_and_preserves_stage(self):
        with patch('project_knowledge.cli.ProjectService') as service, patch('project_knowledge.codex.verify_project_mcp') as probe:
            service.return_value.doctor.return_value = {'codex': {}}
            probe.return_value = {'verified': False, 'stage': 'knowledge_status', 'error': 'missing table'}
            output = io.StringIO()
            with redirect_stdout(output):
                result = main(['doctor', '--verify-mcp', '--json'])
            self.assertEqual(result, 2)
            self.assertIn('knowledge_status', output.getvalue())
            probe.assert_called_once()

    def test_default_doctor_does_not_spawn_probe(self):
        with patch('project_knowledge.cli.ProjectService') as service, patch('project_knowledge.codex.verify_project_mcp') as probe:
            service.return_value.doctor.return_value = {'codex': {}}
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(['doctor', '--json']), 0)
            probe.assert_not_called()
