"""Render static report figures from recomputed tables (system matplotlib)."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

BASE=Path(__file__).resolve().parent/'reports/formal-20261010'
x=json.loads((BASE/'analysis/metrics.json').read_text())
rows=sorted([r for r in x['groups'] if r['stage']=='test'],key=lambda r:r['arm'])
if rows:
    labels=[r['arm'] for r in rows]
    fig,axs=plt.subplots(1,3,figsize=(12,3.5),layout='constrained')
    y=[100*r['mechanicalComplete']['rate'] for r in rows]
    lo=[y[i]-100*r['mechanicalComplete']['low'] for i,r in enumerate(rows)]
    hi=[100*r['mechanicalComplete']['high']-y[i] for i,r in enumerate(rows)]
    axs[0].bar(labels,y,color='#496e85');axs[0].errorbar(labels,y,yerr=[lo,hi],fmt='none',color='#222',capsize=4)
    axs[0].set_ylim(0,110);axs[0].set_title('Mechanical completion / 40 inputs');axs[0].set_ylabel('% (descriptive Wilson 95% CI)')
    for i,r in enumerate(rows):axs[0].text(i,55,f"{r['mechanicalComplete']['successes']}/{r['mechanicalComplete']['n']}",ha='center',color='white')
    positions=list(range(len(rows)))
    axs[1].bar([v-.18 for v in positions],[r['p50Ms']/1000 for r in rows],width=.35,label='P50')
    axs[1].bar([v+.18 for v in positions],[r['p95Ms']/1000 for r in rows],width=.35,label='P95')
    axs[1].set_xticks(positions,labels);axs[1].set_ylabel('seconds; concurrent measured load');axs[1].set_title('End-to-end latency, failures included');axs[1].legend()
    axs[2].bar(labels,[r['reportedEstimateUSD'] for r in rows],color='#967452');axs[2].set_ylabel('USD, list-price estimate');axs[2].set_title('Total API cost per 40 inputs')
    for ax in axs:ax.spines[['top','right']].set_visible(False)
    out=BASE/'figures';out.mkdir(exist_ok=True);fig.savefig(out/'test-engineering.png',dpi=160);fig.savefig(out/'test-engineering.svg');plt.close(fig)
q=json.loads((BASE/'analysis/quality.json').read_text()) if (BASE/'analysis/quality.json').exists() else {'groups':[]}
rs=sorted([r for r in q['groups'] if r['role']=='primary' and r['stage']=='test' and r['category']=='life'],key=lambda r:r['arm'])
if rs:
    dims=['understanding','knowledge','integration','conversation','helpfulness'];fig,ax=plt.subplots(figsize=(10,4),layout='constrained')
    for j,r in enumerate(rs):
        values=[r['dimensions'][d]['mean'] for d in dims]
        ax.plot(range(5),values,marker='o',label=r['arm'])
    ax.set_xticks(range(5),['Understanding','Classical accuracy','Analogy','Conversation','Helpfulness']);ax.set_ylim(0,2.12)
    ax.set_ylabel('Independent non-expert AI mean, 0–2');ax.set_title('Test life questions: scored outputs only; failures shown separately')
    ax.spines[['top','right']].set_visible(False);ax.legend();out=BASE/'figures';out.mkdir(exist_ok=True);fig.savefig(out/'test-life-quality.png',dpi=160);fig.savefig(out/'test-life-quality.svg');plt.close(fig)

# Matplotlib path lines contain insignificant trailing spaces.
for path in (BASE/'figures').glob('*.svg'):
    path.write_text('\n'.join(line.rstrip() for line in path.read_text().splitlines())+'\n')
