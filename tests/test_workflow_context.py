import json
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Projects
from schema import StoreError
from workflow_context import build_snapshot, freshness, completion_projection

PROBLEM = {'title': 'Synthetic workflow fixture', 'statement': 'Test record integrity', 'quantifiers': 'Fixed strings',
           'domain': 'Synthetic data', 'conventions': [], 'target_type': 'exploratory', 'parent': None, 'relation': '',
           'completion_criteria': ['Retain exact bytes'], 'sources': ['Synthetic fixture'], 'knowledge_status': 'synthetic_fixture'}

class WorkflowContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'data'
        self.projects = Projects(self.root)
        self.project = self.projects.create('session-test', deepcopy(PROBLEM))['id']
        self.spec = {'problem_ref': {'id': 'P', 'rev': 1}, 'stage_goal': 'Test context',
                     'model_policy': {'provider': 'fixture', 'model': 'fixture', 'effort': 'fixture'},
                     'context_budget': {'max_tokens': 30000}}

    def tearDown(self):
        self.temp.cleanup()

    def save(self, rev=0, body='Private proof to exclude in reconstruction'):
        data = {'note_type': 'argument', 'title': 'Claim', 'summary': 'Summary', 'body': body,
                'statement': 'Test statement', 'assumptions': ['Keep this premise'], 'problem_ref': {'id':'P','rev':1},
                'route_id': None, 'uses': [], 'candidate_for': [], 'evidence': [], 'gaps': ['Unproved'], 'failure': None}
        self.projects.mutate(self.project, 'commit', {'operation_id': 'save-'+str(rev), 'changes':
            [{'kind':'Note','id':'N','expected_rev':rev,'data':data}]})

    def test_blind_reconstruction_and_bound(self):
        self.save()
        snapshot = build_snapshot(self.root,self.project,self.spec,'reconstruct',{'id':'N','rev':1})
        self.assertNotIn('Private proof',snapshot['text'])
        self.assertIn('Keep this premise',snapshot['text'])
        self.assertEqual(len(snapshot['refs']),2)
        self.spec['context_budget']['max_tokens']=100
        with self.assertRaises(StoreError) as error:
            build_snapshot(self.root,self.project,self.spec,'derive')
        self.assertEqual(error.exception.code,'CONTEXT_LIMIT')

    def commit(self, kind, object_id, data, rev=0):
        if kind == 'Evidence':
            return self.projects.mutate(self.project, 'evidence', {'operation_id': f'{kind}-{object_id}-{rev}',
                'id': object_id, 'expected_rev': rev, 'data': data, 'excerpt': None, 'logical_name': 'fixture.txt'})
        self.projects.mutate(self.project, 'commit', {'operation_id': f'{kind}-{object_id}-{rev}', 'changes':
            [{'kind': kind, 'id': object_id, 'expected_rev': rev, 'data': data}]})

    def test_blind_hidden_evidence_routes_and_explicit_dependencies(self):
        self.save()
        evidence = {'evidence_type': 'source_excerpt', 'problem_ref': {'id': 'P', 'rev': 1},
                    'attachments': [], 'source': {'locator': 'SECRET locator', 'version': None,
                    'accessed_at': None, 'conditions': []}, 'source_status': 'unverified_source',
                    'support_scope': 'SECRET proof strategy', 'limits': ['Synthetic fixture'],
                    'production': {'method': 'fixture', 'version': '1'}, 'citation': None}
        self.commit('Evidence', 'E', evidence)
        with self.projects.open_project(self.project) as (_, store):
            target = store.get('N', 1)['data']
        premise = deepcopy(target)
        premise.update(title='Declared premise', body='Premise body available', statement='Premise statement')
        self.commit('Note', 'PREMISE', premise)
        target.update(title='SECRET title', summary='SECRET summary', body='SECRET original proof',
                      uses=[{'id': 'PREMISE', 'rev': 1}], evidence=[{'id': 'E', 'rev': 1}])
        self.commit('Note', 'N', target, 1)
        self.spec.update(stage_goal='SECRET stage hint', routes=[{'id': 'SECRET-route', 'title': 'SECRET route title',
                         'goal': 'SECRET route goal', 'stop_condition': 'SECRET stop', 'reopen_condition': 'SECRET reopen'}])
        snapshot = build_snapshot(self.root, self.project, self.spec, 'reconstruct', {'id': 'N', 'rev': 2}, route_id='SECRET-route')
        self.assertNotIn('SECRET', snapshot['text'])
        self.spec['stage_goal'] = 'Different original-proof hint'
        without_route = build_snapshot(self.root, self.project, self.spec, 'reconstruct', {'id': 'N', 'rev': 2})
        self.assertEqual(snapshot['text'], without_route['text'])
        self.assertEqual(snapshot['verification_key'], without_route['verification_key'])
        self.assertIn('Premise body available', snapshot['text'])
        payload = json.loads(snapshot['text'])
        self.assertEqual(payload['directory'], [])
        self.assertIsNone(payload['route'])
        self.assertIsNone(payload['action']['route_id'])
        self.assertIsNone(payload['change_templates']['Note']['route_id'])
        self.assertIn({'id': 'E', 'rev': 1}, snapshot['refs'])
        self.assertNotIn('E', [obj['id'] for obj in payload['records']])
        with self.assertRaisesRegex(StoreError, 'forbids explicit dependency_refs'):
            build_snapshot(self.root, self.project, self.spec, 'reconstruct', {'id': 'N', 'rev': 2}, [{'id': 'E', 'rev': 1}])
        regular = build_snapshot(self.root, self.project, self.spec, 'check', {'id': 'N', 'rev': 2})
        self.assertIn('SECRET proof strategy', regular['text'])
        evidence['support_scope'] = 'Changed hidden proof strategy'
        self.commit('Evidence', 'E', evidence, 1)
        with self.projects.open_project(self.project) as (_, store):
            self.assertEqual(freshness(store.records, snapshot), [{'ref': {'id': 'E', 'rev': 1},
                             'state': 'needs_recheck', 'reason': 'dependency_revision_changed'}])
            self.assertEqual(freshness(store.records, regular), freshness(store.records, snapshot))

    def test_blind_rejects_target_evidence_reintroduced_by_a_premise(self):
        self.save()
        evidence = {'evidence_type': 'source_excerpt', 'problem_ref': {'id': 'P', 'rev': 1}, 'attachments': [],
                    'source': {'locator': 'Synthetic', 'version': None, 'accessed_at': None, 'conditions': []},
                    'source_status': 'unverified_source', 'support_scope': 'Proof metadata', 'limits': ['Fixture'],
                    'production': {'method': 'fixture', 'version': '1'}, 'citation': None}
        self.commit('Evidence', 'E', evidence)
        with self.projects.open_project(self.project) as (_, store):
            target = store.get('N', 1)['data']
        target['evidence'] = [{'id': 'E', 'rev': 1}]
        self.commit('Note', 'PREMISE', target)
        target['uses'] = [{'id': 'PREMISE', 'rev': 1}]
        self.commit('Note', 'N', target, 1)
        with self.assertRaises(StoreError) as caught:
            build_snapshot(self.root, self.project, self.spec, 'reconstruct', {'id': 'N', 'rev': 2})
        self.assertEqual(caught.exception.code, 'BLIND_CONTEXT_CONFLICT')

    def test_parent_problem_is_background_not_an_alternate_target(self):
        child = deepcopy(PROBLEM)
        child.update(title='Child target', statement='Child scope only', parent={'id': 'P', 'rev': 1}, relation='Restricted subproblem')
        self.commit('Problem', 'CHILD', child)
        self.spec['problem_ref'] = {'id': 'CHILD', 'rev': 1}
        snapshot = build_snapshot(self.root, self.project, self.spec, 'derive')
        payload = json.loads(snapshot['text'])
        self.assertEqual(payload['problem_ref'], {'id': 'CHILD', 'rev': 1})
        self.assertEqual(payload['background_problem_refs'], [{'id': 'P', 'rev': 1}])
        self.assertEqual([obj['id'] for obj in payload['records']], ['CHILD', 'P'])
        self.assertEqual(payload['change_templates']['Note']['problem_ref'], {'id': 'CHILD', 'rev': 1})
        sibling = deepcopy(child)
        sibling['statement'] = 'Unrelated sibling scope'
        self.commit('Problem', 'OTHER', sibling)
        with self.assertRaises(StoreError) as caught:
            build_snapshot(self.root, self.project, self.spec, 'derive', dependency_refs=[{'id': 'OTHER', 'rev': 1}])
        self.assertEqual(caught.exception.code, 'TARGET_MISMATCH')
        parent = deepcopy(PROBLEM)
        parent['statement'] = 'Parent revision changed'
        self.commit('Problem', 'P', parent, 1)
        with self.projects.open_project(self.project) as (_, store):
            self.assertEqual(freshness(store.records, snapshot)[0]['ref'], {'id': 'P', 'rev': 1})
        with self.assertRaises(StoreError) as caught:
            build_snapshot(self.root, self.project, self.spec, 'derive')
        self.assertEqual(caught.exception.code, 'STALE_DEPENDENCY')

    def test_stale_dependency_and_projection(self):
        self.save()
        snapshot=build_snapshot(self.root,self.project,self.spec,'check',{'id':'N','rev':1})
        self.save(1,'Replacement proof')
        with self.projects.open_project(self.project) as (_,store):
            self.assertEqual(freshness(store.records,snapshot)[0]['state'],'needs_recheck')
            self.assertFalse(completion_projection(store)[0]['human_accepted'])
        with self.assertRaises(StoreError) as error:
            build_snapshot(self.root,self.project,self.spec,'check',{'id':'N','rev':1})
        self.assertEqual(error.exception.code,'STALE_DEPENDENCY')

if __name__ == '__main__':
    unittest.main()
