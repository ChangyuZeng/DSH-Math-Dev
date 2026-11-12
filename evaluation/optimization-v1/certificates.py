"""Finite synthetic certificate tasks. Host-only; stdlib; never imported by solver.
No reference answers are stored here. Generation seed selects public input only.
This same module can be embedded with one public TASK in a frozen checker script.
"""
import hashlib
import itertools
import json
import math
import random
import sys

D = 1
TASK = None

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)

def fingerprint(row):
    return int.from_bytes(hashlib.sha256(canonical(row).encode()).digest(), 'big')

def mul(a, b, p):
    return [[sum(x*y for x,y in zip(row,col)) % p for col in zip(*b)] for row in a]

def degree(p):
    return p.bit_length()-1

def rem(a,b):
    while a and degree(a)>=degree(b):
        a ^= b << (degree(a)-degree(b))
    return a

def pmul(a,b,f):
    c=0
    while b:
        if b&1: c ^= a
        a <<= 1
        if a.bit_length() == f.bit_length(): a ^= f
        b >>= 1
    return c

def ppow(a,n,f):
    r=1
    while n:
        if n&1:r=pmul(r,a,f)
        a=pmul(a,a,f);n>>=1
    return r

def gcd(a,b):
    while b:a,b=b,rem(a,b)
    return a

def check(task, candidate):
    if type(candidate) is not dict or set(candidate)!={'values'} or type(candidate['values']) is not list:
        raise ValueError('Expected exactly {values: [integers]}')
    v=candidate['values']
    if len(v)>20000 or any(type(x) is not int or abs(x).bit_length()>8192 for x in v):
        raise ValueError('Certificate integer/length bound exceeded')
    k=task['kind']; p=task['parameters']; ok=False
    if k=='projective_plane':
        n=p['n']; q=p['q']; edges=list(zip(v[::2],v[1::2])); adj=[set() for _ in range(n)]
        if len(v)!=n*(q+1) or len(set(tuple(sorted(e)) for e in edges))!=len(edges):
            raise ValueError('Wrong edge count or duplicate edges')
        for a,b in edges:
            if not 0<=a<b<n:raise ValueError('Use 0 <= a < b < n')
            adj[a].add(b);adj[b].add(a)
        if any(len(a)!=q+1 for a in adj):raise ValueError('Not regular')
        for s in range(n):
            dist={s:0};parent={s:None};queue=[s]
            for a in queue:
                for b in adj[a]:
                    if b not in dist:dist[b]=dist[a]+1;parent[b]=a;queue.append(b)
                    elif parent[a]!=b and parent[b]!=a and dist[a]+dist[b]+1<6:
                        raise ValueError('Cycle of length below 6')
        ok=True
    elif k=='ramsey_coloring':
        n=p['n'];size=p['clique_size']
        if len(v)!=n*(n-1)//2 or any(x not in (0,1) for x in v):
            raise ValueError('Expected one 0/1 color per edge in i<j lexicographic order')
        adjacency=[[0]*n for _ in range(2)];index=0
        for i in range(n):
            for j in range(i+1,n):
                color=v[index];index+=1
                adjacency[color][i] |= 1<<j
                adjacency[color][j] |= 1<<i
        def has_clique(neighbors, candidates, needed):
            if needed==0:return True
            while candidates.bit_count()>=needed:
                bit=candidates & -candidates;candidates ^= bit
                if has_clique(neighbors,candidates & neighbors[bit.bit_length()-1],needed-1):
                    return True
            return False
        # Each increasing vertex tuple is searched via common-neighbor bitsets.
        # Evaluate both colors even when the first already contains a clique.
        forbidden=[has_clique(neighbors,(1<<n)-1,size) for neighbors in adjacency]
        ok=not any(forbidden)
    elif k=='cyclic_difference_set':
        modulus=p['v'];size=p['k'];multiplicity=p['lambda']
        if len(v)!=size or len(set(v))!=size or any(not 0<=x<modulus for x in v):
            raise ValueError('Expected k distinct residues in 0..v-1')
        counts=[0]*modulus
        for a in v:
            for b in v:
                if a!=b:counts[(a-b)%modulus]+=1
        ok=all(count==multiplicity for count in counts[1:])
    elif k=='turan_k4_3':
        n=p['n'];triples=list(itertools.combinations(range(n),3))
        if len(v)!=len(triples) or any(x not in (0,1) for x in v):
            raise ValueError('Expected one 0/1 indicator per triple in i<j<k lexicographic order')
        edges={triple for triple,bit in zip(triples,v) if bit}
        forbidden=any(all(triple in edges for triple in itertools.combinations(vertices,3))
                      for vertices in itertools.combinations(range(n),4))
        ok=len(edges)>=p['min_edges'] and not forbidden
    elif k=='magic_square':
        n=p['n'];target=n*(n*n+1)//2
        if len(v)!=n*n or set(v)!=set(range(1,n*n+1)):raise ValueError('Expected a normal square permutation')
        sums=[sum(v[i*n:(i+1)*n]) for i in range(n)]+[sum(v[j::n]) for j in range(n)]
        sums += [sum(v[i*n+i] for i in range(n)),sum(v[i*n+n-1-i] for i in range(n))]
        ok=all(s==target for s in sums)
    elif k=='cyclic_sequence':
        n=p['n']
        if len(v)!=n or any(x not in (-1,1) for x in v):raise ValueError('Expected n signs')
        score=max(abs(sum(v[i]*v[(i+s)%n] for i in range(n))) for s in range(1,n))
        return {'H':score,'D':1,'target_met':score<=1,'objective':'maximum absolute nonzero cyclic autocorrelation'}
    elif k=='costas':
        n=p['n']
        if len(v)!=n or set(v)!=set(range(n)):raise ValueError('Expected permutation of 0..n-1')
        ok=all(len({v[i+d]-v[i] for i in range(n-d)})==n-d for d in range(1,n))
    elif k=='partition_dp':
        n=p['N']; m=p['m']; row=[1]+[0]*n; hashes=[]
        for i in range(1,m+1):
            row=[sum(row[s-j*i] for j in (0,1,2) if s>=j*i) for s in range(n+1)]
            hashes.append(fingerprint(row))
        ok=v==[row[n],*hashes]
    elif k=='matrix_power':
        a=p['A'];mod=p['p'];exp=p['exponent'];n=len(a)
        r=[[int(i==j) for j in range(n)] for i in range(n)]; hashes=[]
        while exp:
            hashes.append(fingerprint(a))
            if exp&1:r=mul(r,a,mod)
            a=mul(a,a,mod);exp>>=1
        result=[sum(r[i][j]*p['b'][j] for j in range(n))%mod for i in range(n)]
        ok=v==[*result,*hashes]
    elif k=='pell':
        if len(v)!=2 or min(v)<=0:raise ValueError('Expected positive x,y')
        x,y=v;d=p['d'];a0=math.isqrt(d);m=0;den=1;a=a0;hn2=0;hn1=1;kn2=1;kn1=0
        while True:
            h=a*hn1+hn2;k0=a*kn1+kn2
            if h*h-d*k0*k0==1:break
            hn2,hn1=hn1,h;kn2,kn1=kn1,k0;m=den*a-m;den=(d-m*m)//den;a=(a0+m)//den
        ok=(x,y)==(h,k0)
    elif k=='domino':
        n=p['n'];edges=list(zip(v[::2],v[1::2]));covered=[]
        for a,b in edges:
            if not 0<=a<n*n or not 0<=b<n*n or abs(a//n-b//n)+abs(a%n-b%n)!=1:
                raise ValueError('Each domino must join adjacent cells')
            covered += [a,b]
        ok=len(v)==n*n and set(covered)==set(range(n*n))
    elif k=='primitive_poly':
        n=p['degree']
        if len(v)!=1:raise ValueError('Expected one polynomial bit mask')
        f=v[0]
        if degree(f)!=n or not f&1:raise ValueError('Wrong degree or zero constant coefficient')
        # Rabin irreducibility, then primitive residue x; factoring 2^n-1 by trial division.
        a=2;irreducible=True
        for i in range(1,n+1):
            a=pmul(a,a,f)
            if i<=n//2 and gcd(a^2,f)!=1:irreducible=False
        order=(1<<n)-1;t=order;factors=[];d=2
        while d*d<=t:
            if t%d==0:
                factors.append(d)
                while t%d==0:t//=d
            d+=1
        if t>1:factors.append(t)
        ok=irreducible and a==2 and all(ppow(2,order//r,f)!=1 for r in factors)
    elif k=='crt':
        mods=p['mods'];res=p['res'];product=math.prod(mods)
        ok=len(v)==1 and 0<=v[0]<product and all(v[0]%m==r for m,r in zip(mods,res))
    elif k=='two_squares':
        ok=len(v)==2 and 0<=v[0]<=v[1] and v[0]**2+v[1]**2==p['prime']
    elif k=='determinant':
        a=[row[:] for row in p['A']];n=len(a);prev=1;sign=1
        for i in range(n-1):
            if not a[i][i]:
                j=next((j for j in range(i+1,n) if a[j][i]),None)
                if j is None:return {'H':0,'D':1,'target_met':v==[0]}
                a[i],a[j]=a[j],a[i];sign=-sign
            pivot=a[i][i]
            for j in range(i+1,n):
                for k0 in range(i+1,n):a[j][k0]=(a[j][k0]*pivot-a[j][i]*a[i][k0])//prev
            prev=pivot
        ok=v==[sign*a[-1][-1]]
    else:raise ValueError('Unknown task kind')
    return {'H':int(ok),'D':1,'target_met':ok,'objective':'exact finite certificate'}

def validate_witness(candidate):
    return check(TASK, candidate)

def cards():
    rng=random.Random(20261105)
    matrix=[[rng.randrange(1000000007) for _ in range(6)] for _ in range(6)]
    specs=[
      ('D1','dev','construction','projective_plane',{'n':26,'q':3},
       'Construct a simple undirected 4-regular graph on vertices 0..25 with girth at least 6. Encode all 52 edges by flattening [a,b] pairs, each a<b. Edge order is free.',
       'Incidence graph of a projective plane of order 3 guarantees existence.'),
      ('D2','dev','construction','magic_square',{'n':16},
       'Construct a normal 16 by 16 magic square using each integer 1..256 exactly once. Every row, column, and both main diagonals must have the same sum. Encode row-major entries.',
       'A doubly even normal magic square exists.'),
      ('D3','dev','iterative_search','cyclic_sequence',{'n':127},
       'Find 127 signs s_i in {-1,1} such that for EVERY shift t=1..126, abs(sum_i s_i*s_((i+t) mod127)) <= 1. Encode signs in order.',
       'Binary maximal-length sequences of period 127 guarantee existence.'),
      ('D4','dev','iterative_search','costas',{'n':22},
       'Find a permutation a_0..a_21 of 0..21 such that for each d=1..21 all 22-d integers a_(i+d)-a_i are distinct. Encode the permutation.',
       'The prime-23 Welch construction guarantees existence.'),
      ('D5','dev','multistep_derivation','partition_dp',{'N':500,'m':45},
       'Compute the coefficient of x^500 in product_(i=1..45)(1+x^i+x^(2i)). Let R_i be the full coefficient row 0..500 of the partial product through i. Certificate values must be [coefficient, hash(R_1),...,hash(R_45)]. Hash means SHA256 of compact JSON array (separators comma and colon, no whitespace), interpreted as a nonnegative big-endian integer. Preserve the intermediate rows in this run workspace so the computation can be resumed.',
       'Finite integer coefficient dynamic programming uniquely defines the certificate.'),
      ('D6','dev','multistep_derivation','matrix_power',{'A':matrix,'p':1000000007,'b':[2,3,5,7,11,13],'exponent':10**12+39},
       'Compute A^e b modulo p for the supplied matrix A, column b, and e. Certificate values are the 6 result coordinates followed by hashes of A^(2^j) mod p for j=0..floor(log2(e)). Hash is SHA256 of compact row-major nested JSON arrays, interpreted as nonnegative big-endian integer. Preserve the squaring chain in this run workspace so it can be resumed.',
       'Finite modular matrix exponentiation uniquely defines the certificate.'),
      ('H1','holdout','diophantine','pell',{'d':661},
       'Find the positive integer solution (x,y) of x^2-661*y^2=1 with the smallest x. Encode [x,y].',
       'Lagrange theorem guarantees a fundamental Pell solution.'),
      ('H2','holdout','exact_cover','domino',{'n':12},
       'Tile a 12 by 12 board with dominoes. Cell (r,c) has index 12*r+c for 0<=r,c<12. Certificate values flatten the 72 unordered pairs of orthogonally adjacent cells. Cover every cell exactly once.',
       'An even rectangular board admits a domino tiling.'),
      ('H3','holdout','finite_field','primitive_poly',{'degree':13},
       'Find a monic primitive polynomial f over F_2 of degree 13. Encode its coefficient bit mask as the single integer sum_i coefficient_i*2^i. Primitive means irreducible and the residue of x has multiplicative order 8191 in F_2[x]/(f).',
       'Finite fields admit primitive elements of every positive extension degree.'),
      ('R1','reserve','number_theory','crt',{'mods':[101,103,107,109],'res':[17,29,31,37]},
       'Find the least nonnegative integer with the supplied residues modulo the supplied pairwise coprime moduli. Encode one integer.',
       'Chinese remainder theorem guarantees a unique residue.'),
      ('R2','reserve','number_theory','two_squares',{'prime':1009},
       'Find integers 0<=a<=b with a^2+b^2=1009. Encode [a,b].',
       '1009 is a prime congruent to 1 modulo 4.'),
      ('R3','reserve','exact_linear_algebra','determinant',{'A':[[rng.randrange(-50,51) for _ in range(12)] for _ in range(12)]},
       'Compute the exact integer determinant of the supplied 12 by 12 matrix. Encode one integer.',
       'A finite integer determinant is uniquely defined.'),
      ('D7','session1_dev','iterative_search','ramsey_coloring',{'n':42,'clique_size':5},
       'Construct a two-coloring of every edge of the complete graph on vertices 0..41 with no monochromatic K5 in either color. Encode exactly 861 values in {0,1}, one for each edge (i,j) in lexicographic order with i<j (i outermost, j increasing). Only one construction is requested; no extremal or optimality claim is requested.',
       'A finite construction task; acceptance checks every possible monochromatic K5 in both colors.'),
      ('S1','session1_acceptance','construction','ramsey_coloring',{'n':17,'clique_size':4},
       'Construct a two-coloring of every edge of the complete graph on vertices 0..16 with no monochromatic K4 in either color. Encode exactly 136 values in {0,1}, one for each edge (i,j) in lexicographic order with i<j (i outermost, j increasing). This is an independent acceptance subgoal and does not count as a subsequent research task.',
       'A finite construction exists; acceptance checks every possible monochromatic K4 in both colors.'),
      ('D8','session2_dev','multistep_derivation','cyclic_difference_set',{'v':1057,'k':33,'lambda':1},
       'Construct a subset of exactly 33 distinct residues in the cyclic group Z/1057Z such that every nonzero residue occurs exactly once among the ordered differences a-b with a and b distinct elements of the subset. Encode the 33 residues as integers in 0..1056; order is free. Only this finite construction is requested.',
       'A finite cyclic difference-set construction is known to exist.'),
      ('D9','session2_dev','open_problem_finite_subtask','turan_k4_3',{'n':30,'min_edges':2350},
       'Construct a 3-uniform hypergraph on vertices 0..29 with at least 2350 edges, such that no four vertices contain all four of their 3-element subsets as edges. Encode exactly 4060 values in {0,1}, one per triple (i,j,k) in lexicographic order with i<j<k (i outermost, then j, then k). Only this finite lower-bound witness is requested; no optimality or asymptotic Turan-density claim is requested.',
       'A finite lower-bound construction is known; the general ordinary tetrahedron Turan-density problem is separate.')]
    # Original reserve R1 (CRT) remains immutable and unregistered.
    # S1 is an acceptance-only subgoal with its own unambiguous identifier.
    result=[]
    for ident,split,family,kind,params,statement,solvable in specs:
        card={'id':ident,'split':split,'family':family,'kind':kind,'parameters':params,
              'statement':statement,'quantifiers':'Find one explicit certificate satisfying all stated conditions.',
              'domain':'Finite integer arrays; exact object domains and ranges are given in the statement.',
              'source':'Synthetic instance selected for this experiment; no external target-answer retrieval.',
              'generation_seed':20261105,'solvability':solvable,'public_baseline':'All-zero array is a nonpassing sanity baseline; no target certificate is supplied.',
              'certificate':'Exactly {"values":[integers]}; no code or explanation in this object.',
              'independent_check':'Fresh Python process; exact integer finite verification, no model judgment.',
              'check_cost':'Polynomial finite enumeration/DP; actual wall and CPU time recorded per invocation.',
              'version':1,'requires_persistent_intermediates':ident in ('D5','D6')}
        if ident in ('D8','D9'):
            card['generation_seed']=None
            card['source']=('Singer, A theorem in finite projective geometry and some applications to number theory (1938), doi:10.1090/S0002-9947-1938-1501951-4; finite cyclic difference-set instance.'
                            if ident=='D8' else
                            'Turan ordinary tetrahedron problem; finite 30-vertex lower-bound instance, distinct from uniform Turan density. See Keevash, Hypergraph Turan Problems (2011), doi:10.1017/CBO9781139004114.004.')
        card['sha256']=hashlib.sha256(canonical(card).encode()).hexdigest();result.append(card)
    return result

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='check-witness':
        try:print(canonical(validate_witness(json.loads(open(sys.argv[2]).read()))))
        except (ValueError,TypeError,KeyError,IndexError) as exc:
            print(canonical({'error':str(exc)}));sys.exit(2)
    elif len(sys.argv)==2 and sys.argv[1]=='cards':
        print(json.dumps(cards(),indent=2,ensure_ascii=False))
    else:raise SystemExit('Expected cards or check-witness FILE')
