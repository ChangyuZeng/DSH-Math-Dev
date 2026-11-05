import importlib.util
import json
from pathlib import Path
import unittest

PATH=Path(__file__).resolve().parents[1]/'evaluation/optimization-v1/certificates.py'
spec=importlib.util.spec_from_file_location('optimization_certificates',PATH)
c=importlib.util.module_from_spec(spec);spec.loader.exec_module(c)

class Certificates(unittest.TestCase):
    def test_cards_split_families_hashes_and_negative_baselines(self):
        cards=c.cards()
        self.assertEqual([sum(t['split']==s for t in cards) for s in ('dev','holdout','reserve')],[6,3,3])
        self.assertEqual(sum(t['requires_persistent_intermediates'] for t in cards),2)
        self.assertFalse(set(t['kind'] for t in cards if t['split']=='dev') & set(t['kind'] for t in cards if t['split']=='holdout'))
        for t in cards:
            bare=dict(t);digest=bare.pop('sha256')
            self.assertEqual(c.hashlib.sha256(c.canonical(bare).encode()).hexdigest(),digest)
            try:self.assertFalse(c.check(t,{'values':[0]})['target_met'])
            except ValueError:pass

    def test_development_construction_and_mutation(self):
        tasks={t['id']:t for t in c.cards()}
        # Finite field incidence, independently constructed from dot products.
        vectors=[]
        for x in c.itertools.product(range(3),repeat=3):
            if not any(x):continue
            lead=next(z for z in x if z)
            if lead==1:vectors.append(x)
        edges=[(i,13+j) for i,x in enumerate(vectors) for j,y in enumerate(vectors) if sum(a*b for a,b in zip(x,y))%3==0]
        v=[z for e in edges for z in e]
        self.assertTrue(c.check(tasks['D1'],{'values':v})['target_met'])
        with self.assertRaises(ValueError):c.check(tasks['D1'],{'values':v[:-2]+v[:2]})
        n=16;v=[]
        for i in range(n):
            for j in range(n):
                a=i*n+j+1;v.append(n*n+1-a if i%4==j%4 or i%4+j%4==3 else a)
        self.assertTrue(c.check(tasks['D2'],{'values':v})['target_met'])
        v[0],v[1]=v[1],v[0]
        self.assertFalse(c.check(tasks['D2'],{'values':v})['target_met'])

    def test_development_search_certificates_and_mutation(self):
        tasks={t['id']:t for t in c.cards()}
        # Exhaustively find a degree-7 primitive polynomial; check its output sequence.
        f=next(f for f in range(129,256,2) if c.check({'kind':'primitive_poly','parameters':{'degree':7}},{'values':[f]})['target_met'])
        x=1;v=[]
        for i in range(127):v.append(1 if x&1 else -1);x=c.pmul(x,2,f)
        self.assertTrue(c.check(tasks['D3'],{'values':v})['target_met'])
        v[0]*=-1
        self.assertFalse(c.check(tasks['D3'],{'values':v})['target_met'])
        g=next(g for g in range(2,23) if len({pow(g,i,23) for i in range(22)})==22)
        v=[pow(g,i,23)-1 for i in range(22)]
        self.assertTrue(c.check(tasks['D4'],{'values':v})['target_met'])
        self.assertFalse(c.check(tasks['D4'],{'values':list(range(22))})['target_met'])

    def test_derivations_against_alternative_formulations_on_small_inputs(self):
        row=[0]*9
        for choices in c.itertools.product(range(3),repeat=4):
            s=sum((i+1)*x for i,x in enumerate(choices))
            if s<9:row[s]+=1
        hashes=[]
        for m in range(1,5):
            r=[0]*9
            for choices in c.itertools.product(range(3),repeat=m):
                s=sum((i+1)*x for i,x in enumerate(choices))
                if s<9:r[s]+=1
            hashes.append(c.fingerprint(r))
        task={'kind':'partition_dp','parameters':{'N':8,'m':4}}
        self.assertTrue(c.check(task,{'values':[row[8],*hashes]})['target_met'])
        self.assertFalse(c.check(task,{'values':[row[8]+1,*hashes]})['target_met'])
        a=[[1,2],[3,4]];p=101;exp=9;b=[5,6];v=b[:]
        for _ in range(exp):v=[sum(x*y for x,y in zip(r,v))%p for r in a]
        powers=[a]
        for _ in range(3):powers.append(c.mul(powers[-1],powers[-1],p))
        task={'kind':'matrix_power','parameters':{'A':a,'p':p,'b':b,'exponent':exp}}
        self.assertTrue(c.check(task,{'values':[*v,*map(c.fingerprint,powers)]})['target_met'])
        self.assertFalse(c.check(task,{'values':[0,*v[1:],*map(c.fingerprint,powers)]})['target_met'])

    def test_other_families_only_toy_positive_no_holdout_positive(self):
        for kind,params,v in [('pell',{'d':2},[3,2]),('domino',{'n':2},[0,1,2,3]),
          ('primitive_poly',{'degree':3},[11]),('crt',{'mods':[3,5],'res':[2,3]},[8]),
          ('two_squares',{'prime':5},[1,2]),('determinant',{'A':[[1,2],[3,4]]},[-2])]:
            self.assertTrue(c.check({'kind':kind,'parameters':params},{'values':v})['target_met'])
        with self.assertRaises(ValueError):c.check({'kind':'pell','parameters':{'d':2}},{'values':[True,2]})

if __name__=='__main__':unittest.main()
