import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import llm
import rules


class RulesTests(unittest.TestCase):
    def test_default_legal(self):
        self.assertTrue(rules.validate(rules.character())['valid'])

    def test_attack_payloads_rejected(self):
        base=rules.character()
        cases=[]
        c=copy.deepcopy(base); c['attributes']={k:14 for k in c['attributes']};cases.append(c)
        c=copy.deepcopy(base); c['traits']=['Fit','Fit'];cases.append(c)
        c=copy.deepcopy(base); c['traits']=['Invincible'];cases.append(c)
        c=copy.deepcopy(base); c['attributes']['IQ']=True;cases.append(c)
        c=copy.deepcopy(base); c['skills']['Stealth']=-20;cases.append(c)
        c=copy.deepcopy(base); c['hp']=999;cases.append(c)
        c=copy.deepcopy(base); c['attributes']={k:8 for k in c['attributes']};cases.append(c)
        c=copy.deepcopy(base); c['attributes']['DX']=14;c['skills']['Stealth']=16;cases.append(c)
        for c in cases:
            with self.subTest(character=c):self.assertFalse(rules.validate(c)['valid'])

    def test_critical_edges(self):
        with patch('rules.secrets.randbelow',side_effect=[5,5,4]):
            self.assertFalse(rules.roll(16)['success'])
        with patch('rules.secrets.randbelow',side_effect=[0,0,0]):
            self.assertTrue(rules.roll(2)['success'])


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.old=engine.DB
        engine.DB=Path(self.tmp.name)/'test.db'
        self.ai=patch('llm.enabled',return_value=False); self.ai.start()
        self.s=engine.create(rules.character(),engine.scenario())
    def tearDown(self):
        engine.DB=self.old;self.ai.stop();self.tmp.cleanup()
    def turn(self,text,key='one',revision=0):return engine.turn(self.s['id'],key,revision,text)
    def test_secrets_not_in_public_state(self):
        self.assertNotIn('secret',self.s['scenario'])
        self.assertNotIn('clue',self.s['scenario'])
    def test_question_no_cost(self):
        s=self.turn('Could I sneak to the customs house?')
        self.assertEqual(s['minutes'],0);self.assertFalse(s['complete'])
    def test_duplicate_no_reroll(self):
        with patch('rules.roll',return_value={'success':False,'dice':[6,6,5]}) as dice:
            one=self.turn('Inspect the docks');two=self.turn('Inspect the docks')
            self.assertEqual(one,two);self.assertEqual(dice.call_count,1)
        with self.assertRaises(ValueError):self.turn('Rest')
    def test_stale_revision_rejected(self):
        self.turn('Rest')
        with self.assertRaises(ValueError):self.turn('Rest','two',0)
    def test_state_survives_reopen(self):
        self.turn('Rest')
        self.assertEqual(engine.read(self.s['id'])['minutes'],30)
    def test_invalid_character_cannot_activate(self):
        c=rules.character();c['attributes']['ST']=100
        with self.assertRaises(ValueError):engine.create(c,engine.scenario())
    def test_progression_and_reward_once(self):
        with patch('rules.roll',return_value={'success':True,'dice':[1,1,1]}):
            self.turn('Inspect the docks')
            s=self.turn('Sneak to the customs house','two',1)
            self.assertTrue(s['complete']);self.assertIn('Courier’s letter',s['inventory'])
            s=self.turn('Sneak to the customs house','three',2)
            self.assertEqual(s['inventory'].count('Courier’s letter'),1)
    def test_generation_failure_no_mutation(self):
        with patch('llm.enabled',return_value=True),patch('llm.generate',side_effect=ValueError('provider failed')):
            with self.assertRaises(ValueError):self.turn('Inspect the docks')
        self.assertEqual(engine.read(self.s['id'])['revision'],0)
    def test_narration_failure_keeps_commit(self):
        with patch('llm.enabled',return_value=True),patch('llm.generate',side_effect=[{'action':'rest'},ValueError('provider failed')]):
            s=self.turn('Rest')
        self.assertEqual(s['revision'],1);self.assertEqual(s['minutes'],30)


if __name__=='__main__':unittest.main()
