from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from project_knowledge.guidance_store import GuidanceStore
from project_knowledge.guidance_workflow import GuidanceWorkflow
from project_knowledge.retrieval import KnowledgeAPI
from project_knowledge.store import KnowledgeStore
from project_knowledge.task_workflow import TaskCompletionWorkflow
from tests import test_guidance_incremental as incremental_fixture


class TaskClosedLoopTests(unittest.TestCase):
    def test_initialization_cannot_reuse_incremental_coverage(self):
        from project_knowledge.initialization import InitializationWorkflow
        change = self.fixture.workflow.changes()
        run = InitializationWorkflow(self.root, client=self.client).start()
        self.assertNotEqual(run['run_id'], change['run_id'])
        self.assertTrue(run['batches'])
        self.assertEqual(run['covered_files'], 0)

    def test_task_binds_initialization_and_follows_batch_progress(self):
        with KnowledgeStore(self.fixture.db) as store:
            store.connection.execute("DELETE FROM metadata WHERE key='guidance_snapshot'")
            store.connection.commit()
        with patch('project_knowledge.initialization.CodeGraphClient', return_value=self.client):
            result = self.complete()
        self.assertTrue(result['task']['initialization_run_id'])
        self.assertEqual(result['next_tool'], 'knowledge_initialization_next')
        self.assertEqual(result['arguments']['runId'], result['task']['initialization_run_id'])
        from project_knowledge.task_workflow import refresh_initialization_tasks
        with KnowledgeStore(self.fixture.db) as store:
            guidance = GuidanceStore(store)
            run = guidance.get_run(result['task']['initialization_run_id'])
            run.status = 'complete'
            with store.transaction():
                guidance.create_run(run)
                refresh_initialization_tasks(store, run.run_id)
            self.assertEqual(guidance.get_task_completion('task-1').generation_status, 'generated')

    def setUp(self):
        self.fixture = incremental_fixture.GuidanceIncrementalTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root
        self.client = self.fixture.client
        self.patcher = patch('project_knowledge.task_workflow.CodeGraphClient', return_value=self.client)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.sync = patch('project_knowledge.service.ProjectService.sync', return_value={'action': 'sync'}).start()
        self.addCleanup(patch.stopall)
        self.workflow = TaskCompletionWorkflow(self.root)

    def complete(self, task_id='task-1', **kwargs):
        return self.workflow.complete(
            task_id, 'Implement login', changed_files=['src/login.lua'],
            tests=[{'command': 'unittest', 'passed': True}],
            development_completed=True, **kwargs,
        )

    def test_hook_can_be_promoted_without_fabricating_user_confirmation(self):
        self.workflow.register_pending('task-1', 'hook observed')
        result = self.complete()
        self.assertTrue(result['task']['development_completed'])
        self.assertFalse(result['user_confirmed'])
        self.assertEqual(result['task']['changed_files'], ['src/login.lua'])
        self.assertEqual(result['next_tool'], 'knowledge_update_submit')
        self.assertTrue(result['change_id'])
        self.assertTrue(result['run_id'])
        self.sync.assert_called_once()

    def test_repeat_has_same_plan_conflict_requires_explicit_retry(self):
        first = self.complete()
        second = self.complete()
        self.assertEqual(first['change_id'], second['change_id'])
        self.assertEqual(first['arguments'], second['arguments'])
        with self.assertRaisesRegex(ValueError, 'conflict'):
            self.workflow.complete('task-1', 'Different task', development_completed=True)

    def test_saved_draft_completes_generation_but_not_review(self):
        task = self.complete()
        content = self.fixture.guide_content()
        from project_knowledge.incremental import IncrementalWorkflow
        draft = IncrementalWorkflow(self.root, client=self.client).submit_update(
            task['change_id'], 'guidance', category_id='login', content=content, task_id='task-1',
        )
        result = self.complete()
        self.assertEqual(result['generation_status'], 'generated')
        self.assertEqual(result['next_action'], 'review_drafts')
        self.assertEqual(len(result['pending_drafts']), 1)
        with KnowledgeStore(self.fixture.db) as store:
            self.assertEqual(json.loads(store.get_meta('guidance_snapshot'))['snapshot_id'], 'snap-base')
            self.assertEqual(GuidanceStore(store).current_version('login').version, 1)
        GuidanceWorkflow(self.root, client=self.client).reject_draft(draft['draft_id'], 'tester', 'revise')
        self.assertEqual(self.complete()['generation_status'], 'pending')

    def test_snapshot_drift_marks_failed_and_retry_rebinds(self):
        self.complete()
        self.client.files[0]['content_hash'] = 'h3'
        result = self.complete()
        self.assertEqual(result['generation_status'], 'failed')
        self.assertEqual(result['next_action'], 'retry_task_completion')
        retried = self.complete(retry=True)
        self.assertEqual(retried['generation_status'], 'pending')
        self.assertEqual(retried['final_snapshot_id'], self.client.snapshot()['snapshot_id'])

    def test_no_change_is_terminal_and_skip_requires_reason(self):
        self.client.files[0]['content_hash'] = 'h1'
        with KnowledgeStore(self.fixture.db) as store:
            baseline = json.loads(store.get_meta('guidance_snapshot'))
            baseline['snapshot_id'] = self.client.snapshot()['snapshot_id']
            store.set_meta('guidance_snapshot', json.dumps(baseline))
            store.connection.commit()
        result = self.workflow.complete('noop', 'No source edit', development_completed=True)
        self.assertEqual(result['generation_status'], 'skipped')
        self.assertEqual(result['next_action'], 'none')
        self.assertEqual(result['skip_reason'], 'no_update')
        with self.assertRaises(ValueError):
            self.workflow.complete('skip', 'Skip', development_completed=True, skip=True)

    def test_old_v4_without_task_table_is_upgraded_by_query_entry(self):
        with KnowledgeStore(self.fixture.db) as store:
            store.connection.execute('DROP TABLE task_completions')
            store.set_meta('schema_version', '4')
            store.connection.commit()
        KnowledgeAPI(self.root)
        with KnowledgeStore(self.fixture.db, readonly=True) as store:
            self.assertEqual(GuidanceStore(store).list_task_completions(), [])
            self.assertNotEqual(store.get_meta('schema_version'), '4')

    def test_failed_sync_is_visible_and_can_retry(self):
        self.sync.side_effect = RuntimeError('CodeGraph unavailable')
        result = self.complete()
        self.assertEqual(result['generation_status'], 'failed')
        self.assertIn('CodeGraph unavailable', result['error'])
        self.assertEqual(result['next_action'], 'retry_task_completion')

    def test_deterministic_sync_preserves_workflow_records(self):
        from project_knowledge.config import ProjectConfig
        from project_knowledge.knowledge import KnowledgeGenerator
        from project_knowledge.incremental import IncrementalWorkflow
        task = self.complete()
        draft = IncrementalWorkflow(self.root, client=self.client).submit_update(
            task['change_id'], 'guidance', category_id='login',
            content=self.fixture.guide_content(), task_id='task-1',
        )
        with KnowledgeStore(self.fixture.db) as store:
            for record in store.all_knowledge():
                for source in record.sources:
                    if source.hash and not source.hash.startswith('sha256:'):
                        source.hash = 'sha256:' + source.hash
                record.source_hashes = {key: 'sha256:' + value if not value.startswith('sha256:') else value
                                        for key, value in record.source_hashes.items()}
                store.upsert_knowledge(record)
            store.connection.commit()
            ids = {item.id for item in store.all_knowledge() if item.id.startswith(('draft.', 'guide.'))}
            with store.transaction():
                KnowledgeGenerator(self.root, ProjectConfig(), store).generate(refresh_generated=False)
            self.assertTrue(ids <= {item.id for item in store.all_knowledge()})
            self.assertIsNotNone(store.get_knowledge('draft.' + draft['draft_id']))


if __name__ == '__main__':
    unittest.main()
