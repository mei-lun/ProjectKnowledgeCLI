import unittest
from pathlib import Path

from project_knowledge.service import AGENTS_BODY


class DefaultKnowledgeWorkflowTests(unittest.TestCase):
    def test_default_install_instructions_cover_generation_without_user_completion(self):
        for action in ('knowledge_initialization_start', 'knowledge_initialization_next',
                       'knowledge_initialization_submit', 'knowledge_draft_save',
                       'knowledge_task_complete', 'developmentCompleted', 'next_tool',
                       'project-kb sync', 'knowledge_draft_confirm'):
            self.assertIn(action, AGENTS_BODY)
        self.assertIn('Do not wait', AGENTS_BODY)
        self.assertIn('explicit user approval', AGENTS_BODY)

    def test_plugin_uses_same_workflow_body(self):
        root = Path(__file__).resolve().parents[1]
        skill = (root / 'plugins/project-knowledge/skills/project-knowledge/SKILL.md').read_text(encoding='utf-8')
        self.assertIn(AGENTS_BODY.strip(), skill)

    def test_hook_requires_stable_task_identity(self):
        root = Path(__file__).resolve().parents[1]
        hook = (root / 'plugins/project-knowledge/hooks/sync_after_task.py').read_text(encoding='utf-8')
        self.assertNotIn('time.time()', hook)


if __name__ == '__main__':
    unittest.main()
