# Fact-check protocol

Load this before grading any claim. It is the first pass's rulebook: the seven ways checked work
went wrong in practice, and the rules that stop each one. Pair it with `tiers` (how good a source
is) and with citecheck's mechanical checks (is the quote really on the page).

Every failure mode below was found by re-checking work that already looked finished. None was
caught by the person doing it, at the time. On one 47-claim report a second pass overturned 5
grades and refined 14. On a 24-claim report filed without a second pass, a nine-checker re-run
changed 15 of 24 grades and found a fabricated author attribution.

## The seven failure modes

### F1: graded against a search snippet instead of the source
A search summary said a speaker's article contradicted his on-air number. The article agreed with
him; the summarizer invented the contradiction.
**Rule:** a snippet is a pointer, never evidence. Fetch the document before grading against it.
"X contradicts Y" requires having read Y.

### F2: the query presupposed the answer
The speaker said "top 20%". The checker searched the top-10% figure, found it, and marked him
wrong. The top-20% figure existed and showed he had understated.
**Rule:** search the claim's own framing and units first. Only when that comes back empty, test
whether it garbled a neighbouring figure.

### F3: graded from memory without searching
A casualty figure was graded "roughly right" from memory. The declassified figures were 1.5 to 2.3
times lower.
**Rule:** no grade from memory. A claim with no search behind it is NOT CHECKED, which is a
legitimate, publishable grade. Confidence is not a source.

### F4: refuted from memory; the refutation was never checked
Four counterexamples from memory knocked down a claim. One did not qualify. The rule's own first
draft then disqualified a fifth counterexample, also from memory, and that disqualification was
wrong too.
**Rule:** a counterexample is a claim and meets the same bar. A wrong refutation looks like
diligence and isn't.

### F5: accepted the speaker's hedge instead of checking
"Best guess" was graded unverified and left. Checking would have shown the answer was public, and
three details were wrong.
**Rule:** a hedge changes how harshly you judge the speaker, never whether you check. Note the
hedge beside the grade, not in place of the search.

### F6: silent omission
A report graded 24 claims and left the other checkable ones out of the table, so its unchecked
count looked small. When they were graded, all six came back against the speaker. Claims skipped
because they look like work are the vague, sweeping ones, which are disproportionately wrong.
**Rule:** list every checkable claim first, then grade each, including as NOT CHECKED. A claim you
chose not to check still gets a row.

### F7: an unreviewed first pass treated as finished
The 24-claim report above was filed with no second pass and changed 15 grades when it got one.
**Rule:** work with no adversarial second pass is provisional and says so in its first ten lines,
with what blocked the second pass. Provisional work is not cited by later work.

## Rule 0: the source goes in the row

Every claim row carries its own source link and the exact words quoted from it. A list of sources
at the bottom proves nothing about any single grade: in a table with no source column, a fetched
claim and a remembered one look identical. One report carried 40 links and none of its 49 rows had
a link bound to it.

| # | Claim | Grade | Source | Note |
|---|---|---|---|---|
| 1 | "80% of their energy is imported" | contradicted | [agency report](https://example.org/report) "self-sufficiency rate: 84.7%" | import share is ~15% |

- An empty source cell means NOT CHECKED. The emptiness is the grade.
- Link the document you read, not a search page or a summary of it.
- Quote the words, don't paraphrase them, and run `check_quote` on each link and quote. A
  `not_found` or `blocked` row is fixed (right link, exact words) or set to NOT CHECKED. Say in
  the note when a paywall or a script-built page defeats the check.
- NOT FOUND still fills the source cell with what was searched: "searched X, Y, Z; no primary
  source". That is the visible difference between NOT FOUND and NOT CHECKED.

## Standing rules

1. Every graded claim has a fetched source in its row, or the grade NOT CHECKED.
2. Fetch the primary document for any grade that cites a specific source. Summaries drift, and
   the drift is invisible.
3. Search the claim's own framing and units before testing alternatives.
4. Check your own counterexamples to the same standard as the claim.
5. When sources conflict, cite the one closest in time to the claim and say the other exists.
6. Re-check every CONTRADICTED grade in the speaker's favour before publishing: one more search in
   the speaker's own words, asking what would make it right. Record the outcome.
7. End with an explicit list of open gaps. An empty list is a statement, not an omission.

## Grades

These are citecheck's `Status` values. Map your own labels onto them with `map_label`.

| Status | Meaning | Source cell holds |
|---|---|---|
| SUPPORTED | Checked; the source supports it | link + quote |
| PARTLY | Directionally right, specifics off | link + quote with the right figure |
| CONTRADICTED | Materially false as stated | link + quote that contradicts it |
| NOT_FOUND | Searched; no adequate source exists | what was searched |
| NOT_CHECKED | Not investigated | nothing |
| BLOCKED | The source refused access | the link, and what refused |
| DISPUTED | Good sources disagree | both links |

NOT_FOUND means the evidence isn't out there; NOT_CHECKED means nobody looked. Never promote either
to a grade, and never average grades into one score.

**The unchecked budget.** If any claim is NOT_CHECKED, the summary carries the count. Above 20% of
graded claims, the work is not publishable as a fact-check: say so in the first ten lines, or go
check them. A claim the conclusion leans on is never NOT_CHECKED.

## The second pass

- Give the finished grades to independent checkers told to refute, not confirm.
- Ask them for grades that were too harsh as well as too generous; only that catches F2. In the
  15 changed grades above, 4 went in the speaker's favour. A checker that only finds the speaker
  wrong has been agreeable in the other direction.
- Record what the second pass changed in the work itself rather than editing silently. Keep each
  changed grade as a labelled row (first grade, final grade, failure mode) so the change can be
  replayed with `python -m citecheck.regress`.
- A wrong hunch left NOT CHECKED is a success: it is what a wrong guess looks like when it doesn't
  reach print.
- More checkers is not more rigour. Discipline in the first pass is what makes the work
  trustworthy; the second pass measures it.
