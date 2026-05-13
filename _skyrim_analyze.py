"""
Skyrim SE review analysis — no API key required.
Outputs stats + bigrams + top 600 reviews for manual analysis.
Playtime thresholds adjusted for Skyrim: <100h / 100-500h / 500h+
"""
import sys, io, re, pandas as pd
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CSV = "skyrim_reviews.csv"
df  = pd.read_csv(CSV, encoding="utf-8-sig")

total = len(df)
eng   = df[df["language"] == "english"]
pos_all = df[df["voted_up"] == True]
neg_all = df[df["voted_up"] == False]

print("=" * 60)
print("SKYRIM SE — FULL STATS")
print("=" * 60)
print(f"Total reviews collected    : {total:,}")
print(f"Positive (recommended)     : {len(pos_all):,} ({len(pos_all)/total*100:.1f}%)")
print(f"Negative (not recommended) : {len(neg_all):,} ({len(neg_all)/total*100:.1f}%)")

# Language distribution
print("\nTop 10 languages:")
for lang, cnt in df["language"].value_counts().head(10).items():
    print(f"  {lang:<20} {cnt:>7,}  ({cnt/total*100:.1f}%)")

# Playtime (Skyrim thresholds: <100h / 100-500h / 500h+)
pt = df["author_playtime_forever_min"].dropna()
print(f"\nAvg playtime  : {pt.mean()/60:.1f}h")
print(f"Median        : {pt.median()/60:.1f}h")
print(f"<100h         : {(pt < 6000).sum():,} ({(pt<6000).sum()/len(pt)*100:.1f}%)")
print(f"100-500h      : {((pt>=6000)&(pt<30000)).sum():,} ({((pt>=6000)&(pt<30000)).sum()/len(pt)*100:.1f}%)")
print(f"500h+         : {(pt >= 30000).sum():,} ({(pt>=30000).sum()/len(pt)*100:.1f}%)")

# English quality filter
q_eng = eng[
    (eng["votes_up"].fillna(0) >= 5) &
    (eng["review"].fillna("").str.len() >= 50)
].copy()
print(f"\nEnglish quality reviews (votes>=5, len>=50): {len(q_eng):,}")

# Top reviews
pos_top = q_eng[q_eng["voted_up"]==True].sort_values("votes_up", ascending=False)
neg_top = q_eng[q_eng["voted_up"]==False].sort_values("votes_up", ascending=False)
print(f"  Top positive : {len(pos_top):,}")
print(f"  Top negative : {len(neg_top):,}")

# Bigrams
STOP = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'is','it','that','this','i','you','my','me','we','they','he','she','was',
    'are','be','have','has','had','not','no','all','so','if','as','by','from',
    'can','will','its','been','do','did','get','just','like','very','really',
    'much','more','most','some','any','than','then','when','what','how','who',
    'out','up','about','after','before','there','their','which','would','could',
    'should','also','still','even','only','back','first','were','been','into',
    'over','through','its','our','your','ive','dont','im','youre','thats',
    'doesnt','didnt','isnt','wasnt','cant','wont','game','games','play',
    'played','playing','player','players','time','skyrim','elder','scrolls',
    'bethesda','special','edition',
}

def bigrams(texts, n=400):
    words_all = []
    for t in texts[:n]:
        clean = re.sub(r'[^a-z\s]', ' ', str(t).lower())
        words = [w for w in clean.split() if w not in STOP and len(w) >= 4]
        words_all.extend(zip(words, words[1:]))
    return Counter(words_all)

pos_bi = bigrams(pos_top["review"].tolist())
neg_bi = bigrams(neg_top["review"].tolist())

print("\n" + "=" * 60)
print("TOP 40 BIGRAMS — POSITIVE REVIEWS")
print("=" * 60)
for bg, cnt in pos_bi.most_common(40):
    print(f"  {' '.join(bg):<32} {cnt}")

print("\n" + "=" * 60)
print("TOP 40 BIGRAMS — NEGATIVE REVIEWS")
print("=" * 60)
for bg, cnt in neg_bi.most_common(40):
    print(f"  {' '.join(bg):<32} {cnt}")

# Top 600 reviews (300 pos + 300 neg), sorted by votes
sample = pd.concat([pos_top.head(300), neg_top.head(300)]).sort_values("votes_up", ascending=False).reset_index(drop=True)
BATCH = 100
for b in range(6):
    chunk = sample.iloc[b*BATCH:(b+1)*BATCH]
    print(f"\n{'='*60}")
    print(f"BATCH {b+1}/6  (rows {b*BATCH+1}-{(b+1)*BATCH})")
    print(f"{'='*60}")
    for _, r in chunk.iterrows():
        tag  = "REC" if r["voted_up"] else "NOT"
        pt_h = int(r["author_playtime_forever_min"]/60) if pd.notna(r["author_playtime_forever_min"]) else "?"
        text = str(r["review"])[:600].replace("\n", " ")
        print(f"[{tag}|votes:{int(r['votes_up'])}|pt:{pt_h}h|lang:{r['language']}] {text}")
        print("---")
