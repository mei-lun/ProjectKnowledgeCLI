from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .codegraph import CodeGraphClient
from .config import ProjectConfig
from .guidance_models import TaskCompletion
from .guidance_store import GuidanceStore
from .store import KnowledgeStore
from .util import utc_now


def refresh_linked_tasks(store: KnowledgeStore, change_id: str) -> None:
    """Refresh persisted generation state inside the caller's transaction."""
    from .incremental import IncrementalWorkflow

    guidance = GuidanceStore(store)
    plan = IncrementalWorkflow.plan_from_store(store, change_id)
    for task in guidance.list_task_completions(limit=None):
        if task.change_id != change_id or task.generation_status in {'skipped', 'failed'}:
            continue
        task.generation_status = 'generated' if plan.get('generation_complete') else 'pending'
        if plan.get('next_action') == 'retry':
            task.generation_status, task.error = 'failed', plan.get('error') or 'snapshot changed'
        task.updated_at = utc_now()
        guidance.save_task_completion(task)


def refresh_initialization_tasks(store: KnowledgeStore, run_id: str) -> None:
    guidance = GuidanceStore(store)
    run = guidance.get_run(run_id)
    if run is None:
        return
    for task in guidance.list_task_completions(limit=None):
        if task.initialization_run_id != run_id or task.generation_status == 'skipped':
            continue
        task.generation_status = 'failed' if run.status == 'failed' else ('generated' if run.status == 'complete' else 'pending')
        task.error = run.error if run.status == 'failed' else None
        task.updated_at = utc_now()
        guidance.save_task_completion(task)


class TaskCompletionWorkflow:
    """Link completed development to change-scoped, reviewable knowledge."""

    def __init__(self, project: str | Path):
        self.root = Path(project).resolve()
        self.config = ProjectConfig.load(self.root)
        self.client = CodeGraphClient(self.root, self.config)
        self.db_path = self.root / '.project-kb' / 'index.db'

    def _open(self) -> KnowledgeStore:
        store = KnowledgeStore(self.db_path)
        store.initialize()
        return store

    def complete(
        self, task_id: str, summary: str, *, changed_files: list[str] | None = None,
        changed_symbols: list[str] | None = None, tests: list[dict[str, Any]] | None = None,
        user_confirmed: bool = False, development_completed: bool = False,
        skip: bool = False, skip_reason: str | None = None, retry: bool = False,
    ) -> dict[str, Any]:
        if not (development_completed or user_confirmed):
            raise ValueError('developmentCompleted or userConfirmed must be true')
        task_id, summary = str(task_id).strip(), str(summary).strip()
        if not task_id or not summary:
            raise ValueError('task_id and summary are required')
        if skip and not str(skip_reason or '').strip():
            raise ValueError('skipReason is required when skipping generation')
        paths = sorted({self._relative_path(path) for path in (changed_files or [])})
        symbols = sorted({str(symbol).strip() for symbol in (changed_symbols or []) if str(symbol).strip()})
        results = [dict(item) for item in (tests or [])]
        request_hash = hashlib.sha256(json.dumps(
            [summary, paths, symbols, results, skip, skip_reason],
            sort_keys=True, ensure_ascii=False, allow_nan=False,
        ).encode()).hexdigest()
        with self._open() as store:
            guidance = GuidanceStore(store)
            existing = guidance.get_task_completion(task_id)
            if existing and existing.development_completed and not retry:
                if existing.request_hash and existing.request_hash != request_hash:
                    raise ValueError('task_id conflict; use a new taskId or explicit retry')
                if existing.change_id or existing.initialization_run_id or existing.generation_status in {'failed', 'skipped'}:
                    return self._current_result(existing, store)
            now = utc_now()
            raw_baseline = store.get_meta('guidance_snapshot')
            baseline = json.loads(raw_baseline) if raw_baseline else {}
            task = TaskCompletion(
                task_id, str(self.root), summary, paths, symbols, results,
                str(baseline.get('snapshot_id') or 'unavailable'), 'unavailable',
                bool(user_confirmed), 'skipped' if skip else 'pending', [],
                existing.created_at if existing else now, now,
                skip_reason=skip_reason if skip else None, development_completed=True,
                request_hash=request_hash,
            )
            with store.transaction():
                guidance.save_task_completion(task)
        if skip:
            with self._open() as store:
                return self._result(task, store)
        try:
            from .incremental import IncrementalWorkflow
            from .service import ProjectService

            ProjectService(self.root).sync(task_summary=summary)
            snapshot = self.client.snapshot()
            task.final_snapshot_id = str(snapshot['snapshot_id'])
            with self._open() as store:
                initialized = bool(store.get_meta('guidance_snapshot')) and bool(GuidanceStore(store).list_categories())
            if initialized:
                incremental = IncrementalWorkflow(self.root, client=self.client)
                change = incremental.changes(retry=retry)
                task.change_id = change.get('change_id')
                task.affected_categories = list(change.get('affected_categories', []))
                if task.change_id:
                    if change.get('head_snapshot_id') != task.final_snapshot_id:
                        raise ValueError('snapshot changed during task completion; retry required')
                else:
                    task.generation_status = 'skipped'
                    task.skip_reason = 'no_update'
            else:
                from .initialization import InitializationWorkflow
                init = InitializationWorkflow(self.root, client=self.client).start()
                task.initialization_run_id = str(init['run_id'])
        except (OSError, RuntimeError, ValueError, KeyError) as error:
            task.generation_status = 'failed'
            task.error = str(error)
        with self._open() as store:
            with store.transaction():
                task.updated_at = utc_now()
                GuidanceStore(store).save_task_completion(task)
                if task.change_id and task.generation_status != 'failed':
                    refresh_linked_tasks(store, task.change_id)
                elif task.initialization_run_id:
                    refresh_initialization_tasks(store, task.initialization_run_id)
            return self._result(GuidanceStore(store).get_task_completion(task_id), store)

    def _relative_path(self, value: str) -> str:
        normalized = str(value).replace('\\', '/').strip()
        candidate = Path(normalized)
        if not normalized or candidate.is_absolute() or '..' in candidate.parts:
            raise ValueError('changedFiles must contain project-relative paths')
        resolved = (self.root / candidate).resolve()
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as error:
            raise ValueError('changedFiles path is outside project') from error

    def _current_result(self, task: TaskCompletion, store: KnowledgeStore) -> dict[str, Any]:
        if task.change_id:
            change = store.connection.execute('SELECT processed_at FROM guidance_changes WHERE change_id=?',
                                              (task.change_id,)).fetchone()
            if change and change['processed_at']:
                return self._result(task, store)
        if (task.change_id or task.initialization_run_id) and task.generation_status not in {'failed', 'skipped'}:
            try:
                if self.client.snapshot()['snapshot_id'] != task.final_snapshot_id:
                    raise ValueError('CodeGraph snapshot changed; retry task completion')
                with store.transaction():
                    if task.change_id:
                        refresh_linked_tasks(store, task.change_id)
                    else:
                        refresh_initialization_tasks(store, task.initialization_run_id)
            except (OSError, RuntimeError, ValueError, KeyError) as error:
                task.generation_status, task.error = 'failed', str(error)
                task.updated_at = utc_now()
                with store.transaction():
                    GuidanceStore(store).save_task_completion(task)
        return self._result(GuidanceStore(store).get_task_completion(task.task_id), store)

    def register_pending(self, task_id: str, summary: str) -> dict[str, Any]:
        task_id, summary = str(task_id).strip(), str(summary).strip()
        if not task_id or not summary:
            raise ValueError('task_id and summary are required')
        with self._open() as store:
            guidance = GuidanceStore(store)
            existing = guidance.get_task_completion(task_id)
            if existing:
                return self._result(existing, store)
            now = utc_now()
            task = TaskCompletion(
                task_id, str(self.root), summary, [], [], [], 'unavailable', 'unavailable',
                False, 'pending', [], now, now,
            )
            with store.transaction():
                guidance.save_task_completion(task)
            return self._result(task, store)

    @staticmethod
    def _result(task: TaskCompletion, store: KnowledgeStore) -> dict[str, Any]:
        from .incremental import IncrementalWorkflow

        guidance = GuidanceStore(store)
        plan: dict[str, Any] = {}
        if task.generation_status == 'skipped':
            action = 'none'
        elif task.generation_status == 'failed':
            action = 'retry_task_completion'
        elif not task.development_completed:
            action = 'complete_development'
        elif task.initialization_run_id:
            plan = TaskCompletionWorkflow._initialization_plan(store, task.initialization_run_id)
            action = plan['next_action']
        elif not task.change_id:
            action = 'start_initialization'
            plan = {'next_tool': 'knowledge_initialization_start', 'arguments': {}}
        else:
            plan = IncrementalWorkflow.plan_from_store(store, task.change_id)
            action = plan.get('next_action', 'inspect_changes')
            if plan.get('generation_complete'):
                action = 'review_drafts' if action not in {'complete', 'none'} else 'none'
            if plan.get('next_tool') == 'knowledge_update_submit':
                plan['arguments'] = {**plan.get('arguments', {}), 'taskId': task.task_id}
        if action in {'retry_task_completion', 'complete_development'}:
            plan = {'next_tool': 'knowledge_task_complete', 'arguments': {
                'taskId': task.task_id, 'summary': task.summary,
                'changedFiles': task.changed_files, 'changedSymbols': task.changed_symbols,
                'tests': task.tests, 'developmentCompleted': True,
                **({'retry': True} if action == 'retry_task_completion' else {}),
            }}
        return {
            **plan, 'task_id': task.task_id, 'generation_status': task.generation_status,
            'user_confirmed': task.user_confirmed, 'development_completed': task.development_completed,
            'final_snapshot_id': task.final_snapshot_id, 'change_id': task.change_id,
            'initialization_run_id': task.initialization_run_id,
            'affected_categories': task.affected_categories, 'summary': task.summary,
            'skip_reason': task.skip_reason, 'error': task.error, 'task': task.to_dict(),
            'next_action': action,
            'pending_drafts': [draft.to_dict() for draft in guidance.list_pending_drafts()
                               if draft.payload.get('_task_id') == task.task_id or
                               (task.initialization_run_id and draft.run_id == task.initialization_run_id)],
        }

    @staticmethod
    def _initialization_plan(store: KnowledgeStore, run_id: str) -> dict[str, Any]:
        from .retrieval import KnowledgeAPI
        guidance = GuidanceStore(store)
        run = guidance.get_run(run_id)
        if not run or run.status == 'failed':
            return {'next_action': 'retry_task_completion'}
        categories = guidance.list_categories(run_id)
        formal_assets = {(item.category_id, asset_type) for item in categories
                         for asset_type in ('methodology', 'project_guidance')
                         if guidance.current_version(item.category_id, asset_type)}
        state = KnowledgeAPI._workflow_state(run, guidance.list_batches(run_id),
                                            guidance.list_pending_drafts(run_id), [], categories, False, formal_assets)
        action = state['next_action']
        result = {'run_id': run_id, **state}
        if action == 'analyze_next_batch':
            result.update(next_tool='knowledge_initialization_next', arguments={'runId': run_id})
        elif action in {'create_category_draft', 'create_methodology', 'create_guidance'}:
            item = state.get('next_draft', {'kind': 'category_catalog'})
            result.update(next_tool='knowledge_draft_save', arguments={
                'action': 'save', 'runId': run_id, 'kind': item['kind'],
                **({'categoryId': item['category_id']} if item.get('category_id') else {}),
            })
        elif action in {'none', 'draft_available'}:
            result['next_action'] = 'review_drafts' if guidance.list_pending_drafts(run_id) else 'none'
        return result

    def plan(self, selected: list[str] | None = None) -> dict[str, Any]:
        with self._open() as store:
            guidance = GuidanceStore(store)
            categories = guidance.list_categories()
            available = {category.category_id for category in categories}
            selected_ids = sorted({str(item) for item in (selected or [])})
            if set(selected_ids) - available:
                raise ValueError('selected category does not exist')
            return {
                'categories': [category.to_dict() for category in categories], 'selected': selected_ids,
                'next_drafts': [{'category_id': category_id, 'kind': kind}
                                for category_id in selected_ids for kind in ('methodology', 'guidance')],
                'next_action': 'generate_methodology' if selected_ids else ('select_categories' if categories else 'start_initialization'),
            }
