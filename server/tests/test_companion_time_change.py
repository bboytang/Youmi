"""Literal natural time updates preserve history; they are not past corrections."""

import sqlite3
import subprocess
import unittest
from unittest.mock import patch

from server.companion import emotional_system
from server.companion.extraction import context_for,validate_output,natural_change_requested
from server.companion.memory import MemoryError
from server.tests import test_companion_context as fixture
from server.tests import test_companion_reflection_generation as sdk_fixture

OLD='我每周二晚上练钢琴，练完总觉得很开心。'
NEW='我现在改成周四晚上练钢琴，周二不练了；练完仍然很开心。'


def proposal(identifier,old=OLD,source=NEW):
    return dict(category='fact',content=source,source_quote=source,source_index=0,kind='explicit',subject='user',
        value='durable',sensitive=False,entities=['钢琴'],keywords=['周四'],trigger_terms=['练钢琴'],
        reconciliation=dict(form='natural',kind='time_change',target_id=identifier,old_quote=old,evidence=source,
            old_value='周二',new_value='周四',anchor='练钢琴'))


class NaturalTimeChangeTests(unittest.TestCase):
    setUp=fixture.ContextBuilderTests.setUp
    tearDown=fixture.ContextBuilderTests.tearDown
    fact=fixture.ContextBuilderTests.fact

    def seeded(self):
        identifier=self.fact(OLD)
        return identifier,context_for(self.store,self.scope.owner)

    def test_frozen_old_guard_filters_the_complete_source_but_new_guard_validates_time_change(self):
        identifier,context=self.seeded()
        namespace=dict(__name__='server.companion.frozen_extraction',__package__='server.companion')
        source=subprocess.check_output(['git','show','090bfe9b:server/companion/extraction.py'],text=True)
        exec(compile(source,'frozen-extraction-090bfe9b.py','exec'),namespace)
        raw=dict(memories=[proposal(identifier)])
        self.assertEqual(namespace['validate_output'](raw,[NEW],context)['memories'],[])
        self.assertTrue(natural_change_requested(NEW,context))
        result=validate_output(raw,[NEW],context)
        self.assertEqual(result['memories'][0]['reconciliation']['kind'],'time_change')
        self.assertEqual(result['memories'][0]['reconciliation']['target_id'],identifier)

    def test_negation_uncertainty_and_other_activities_never_retire_old_fact(self):
        identifier,context=self.seeded()
        sources=[NEW.replace('我现在','如果我现在'),NEW.replace('我现在','他说我现在'),
            NEW.replace('改成周四','打算改成周四'),NEW.replace('周二不练了','周二不取消练琴'),
            NEW.replace('周二不练了','周二不是不练'),NEW.replace('周四晚上练','周四晚上不练'),
            NEW.replace('周二不练了','周二不跑步了'),NEW.replace('改成周四','可能改成周四'),NEW.replace('练完仍然很开心','只是这一次临时调整')]
        before=list(self.store.db.iterdump())
        for source in sources:
            with self.subTest(source=source):
                try:result=validate_output(dict(memories=[proposal(identifier,source=source)]),[source],context)
                except MemoryError:continue
                self.assertEqual(result['memories'],[])
        self.assertEqual(list(self.store.db.iterdump()),before)

    def test_foreign_ambiguous_and_fabricated_values_are_rejected(self):
        identifier,context=self.seeded()
        foreign=self.fact(OLD,owner='user:2:agent:foreign')
        for change in (dict(target_id=foreign),dict(old_value='周三'),dict(new_value='周五'),dict(anchor='周二')):
            item=proposal(identifier);item['reconciliation'].update(change)
            with self.subTest(change=change),self.assertRaises(MemoryError):
                validate_output(dict(memories=[item]),[NEW],context)
        self.fact('我每周二练钢琴，使用另一把琴')
        with self.assertRaises(MemoryError):
            validate_output(dict(memories=[proposal(identifier)]),[NEW],context_for(self.store,self.scope.owner))

    def test_an_old_negative_statement_is_not_treated_as_the_old_positive_schedule(self):
        old=OLD.replace('晚上练钢琴','晚上不练钢琴')
        identifier=self.fact(old)
        with self.assertRaises(MemoryError):
            validate_output(dict(memories=[proposal(identifier,old=old)]),[NEW],context_for(self.store,self.scope.owner))

    def test_stale_source_versions_preserve_current_fact(self):
        identifier,context=self.seeded()
        validated=validate_output(dict(memories=[proposal(identifier)]),[NEW],context)
        revision=self.store.snapshot(self.scope.owner)['revision']
        self.store.edit_memory(self.scope.owner,identifier,'我周二晚上练钢琴，管理员已明确改文')
        before=list(self.store.db.iterdump())
        result=self.store.apply_extraction(self.scope.owner,'changed',revision,[NEW],validated,'stale')
        self.assertFalse(result['applied'])
        self.assertEqual(list(self.store.db.iterdump()),before)


class NaturalTimeSDKTests(unittest.IsolatedAsyncioTestCase):
    setUp=sdk_fixture.ReflectionGenerationTests.setUp
    tearDown=sdk_fixture.ReflectionGenerationTests.tearDown
    event=sdk_fixture.ReflectionGenerationTests.event
    row=sdk_fixture.ReflectionGenerationTests.row
    requests=sdk_fixture.ReflectionGenerationTests.requests
    seed=sdk_fixture.ReflectionGenerationTests.seed
    enable=sdk_fixture.ReflectionGenerationTests.enable
    capture_request=sdk_fixture.ReflectionGenerationTests.capture_request
    run_event=sdk_fixture.ReflectionGenerationTests.run_event

    async def test_same_sdk_request_revises_current_schedule_without_rewriting_old_history(self):
        old=self.seed(OLD)
        sources=[tuple(row) for row in self.store.db.execute('SELECT * FROM sources WHERE memory_id=?',(old['id'],))]
        emotion=emotional_system.status(self.store,self.scope.owner)
        await self.enable();self.capture_request()
        self.output=dict(reflection_version=1,memory_actions=[dict(action='revise',memory=proposal(old['id']))])
        job=await self.run_event(text=NEW)
        self.assertEqual(self.row(job)['state'],'committed')
        self.assertEqual(len(self.http),1)
        tool=self.bodies[0]['tools'][0]['function']['parameters']['properties']['memory_actions']['items']['oneOf']
        revise=next(item for item in tool if item['properties']['action']['const']=='revise')
        self.assertEqual(revise['properties']['memory']['properties']['reconciliation']['properties']['kind']['enum'],['time_change'])
        old_after=self.store.db.execute('SELECT m.content,m.confirmed,x.state FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.id=?',(old['id'],)).fetchone()
        self.assertEqual(tuple(old_after),(OLD,0,'superseded'))
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM sources WHERE memory_id=?',(old['id'],))],sources)
        active=self.store.db.execute("SELECT id,content FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.owner=? AND m.confirmed=1 AND x.state='active'",(self.scope.owner,)).fetchall()
        self.assertEqual(len(active),1);self.assertNotEqual(active[0]['id'],old['id']);self.assertIn('周四',active[0]['content'])
        self.assertFalse(await self.worker.run_once());self.assertEqual(len(self.http),1)
        self.assertEqual(emotional_system.status(self.store,self.scope.owner),emotion)

    async def test_storage_failure_does_not_partially_retire_old_fact(self):
        old=self.seed(OLD)
        sources=[tuple(row) for row in self.store.db.execute('SELECT * FROM sources WHERE memory_id=?',(old['id'],))]
        await self.enable();self.capture_request()
        self.output=dict(reflection_version=1,memory_actions=[dict(action='revise',memory=proposal(old['id']))])
        original=self.jobs.commit
        def fail(job,result,vectors=None):
            self.store.db.set_authorizer(lambda action,name,*args: sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_UPDATE and name=='mia_memory_meta' else sqlite3.SQLITE_OK)
            try:return original(job,result,vectors)
            finally:self.store.db.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
        with patch.object(self.jobs,'commit',side_effect=fail):job=await self.run_event(text=NEW)
        self.assertEqual(self.row(job)['state'],'retry_wait')
        self.assertEqual(self.row(job)['error_category'],'database')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM memories').fetchone()[0],1)
        old_after=self.store.db.execute('SELECT m.content,m.confirmed,x.state FROM memories m JOIN mia_memory_meta x ON x.memory_id=m.id WHERE m.id=?',(old['id'],)).fetchone()
        self.assertEqual(tuple(old_after),(OLD,1,'active'))
        self.assertEqual([tuple(row) for row in self.store.db.execute('SELECT * FROM sources WHERE memory_id=?',(old['id'],))],sources)
        self.assertEqual(len(self.http),1)
