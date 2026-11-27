# Source tiers

Load this before citing a source. Every fact carries the tier of the source it came from, written
beside it (`S1`, `S2`, `S3`), so a reader can see how much weight it bears. The tier goes in the
`tier` field of a citecheck `Claim` or `Citation`.

| Tier | What | Examples | Good for |
|---|---|---|---|
| S1 | Primary, authoritative: the body that sets or records the fact | statute, regulator, government database, recorded deed, court filing, the paper itself, the maker's or vendor's own spec sheet or price list | any fact, including one a decision rests on |
| S2 | Professional or institutional, one step removed | trade body, professional guide, established news outlet, a firm's published research | most facts; check against S1 before a decision |
| S3 | Secondary: compiles or retells others | books, aggregators and spec databases, encyclopedias, blogs, summaries | leads, background, what to look up next |
| Forum | People reporting their own experience | forums, reviews, comment threads, social posts | what breaks and what it's like; never a number |

## Rules

1. **A decision that costs money, time or safety rests on S1.** S2 and S3 tell you where to look.
   Write a move up a tier as `S2→S1` once the primary source confirms it.
2. **A search snippet, a model's answer or a summary has no tier.** It is a pointer to a source.
   Fetch the source, then tier that.
3. **Numbers come from S1 or S2.** Prices, rates, specs, dates and counts from S3 or a forum are
   marked unverified until a higher tier confirms them.
4. **Every number carries a date.** Prices and rates move; a figure without the date it was
   checked is unverified.
5. **Unknown stays unknown.** No source, no fact: mark it NOT_CHECKED (nobody looked) or NOT_FOUND
   (looked, nothing there) and say what was searched. Never fill the gap with a plausible guess,
   and never count a missing source as a pass.
6. **Tier the source you read, not the one it cites.** A news story about a study is S2 even if the
   study is S1; read the study to cite it as S1.
7. **When tiers conflict, the higher tier wins,** and the lower one is still named. When two
   sources of the same tier conflict, the grade is DISPUTED.
8. **Corrections stay visible.** When a fact turns out wrong, mark the correction where the fact
   was rather than quietly replacing it.
