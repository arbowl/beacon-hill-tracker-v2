+++
title = "Why the Compliance Rate Dropped from 52% to 37.6%"
published = 2026-11-22
category = "analysis"
summary = "The new algorithm reports a lower compliance rate than the old site. Here's where the difference comes from, what I got wrong before, and why the new number is the better one."
+++

When the new Tracker launched, the headline compliance rate fell from about 52% on the old site to 37.6%. That's a big swing, and anyone who cited the old number deserves an explanation.

The short version: The change reflects improvements in measurement, not a sudden change in legislative behavior. The old number was inflated by problems in the old tool. They were my mistakes, and this post walks through each one.

## How the rate is calculated
Both versions use the same formula: the share of bill segments that are **not** Non-Compliant. A segment is one bill as handled by one committee. Compliant segments count toward the rate, and so do Provisional ones (meaning the deadline hasn't passed or there isn't enough data to judge yet).

That second part is the key: Anything the tool can't judge counts in a committee's favor, so how many of those segments exist moves the rate a lot.

## Where the difference comes from
I compared the old and new algorithms bill by bill on the same day, November 20, 2026. Each row below changes one thing, in order:

| Step | Rate |
|---|---|
| Old site, as displayed | ~52.0% |
| Removing bill segments with no hearing and no vote | 26.8% |
| Correcting bills the old tool mislabeled as Compliant | 17.5% |
| Applying the new rules to bills both versions checked | 17.5% |
| Adding joint committee bills the old tool never listed | 22.5% |
| Adding every House and Senate committee bill | 37.6% |

1. **Procedural transits:** Many bill segments exist only for a short time in a House or Senate committe's tenure. These segments contain no hearing, no vote, and are often quick turnarounds. These contain no true compliance content to analyze, so pruning these collapses many Provisional bill segments into few.
2. **Mislabeled bills:** The old tool marked 703 joint committee bills Compliant even though the data showed they were reported out after their deadline. Fixing those lowers the rate by 9.3 points.
3. **Coverage:** The old tool missed some bill pages for House and Senate committees. The new one checks every bill: 2,320 House and Senate segments, nearly all of them passing or still pending. It also picks up 1,112 joint committee segments the old tool never listed. Together these *raise* the rate by about 20 points.

The new Tracker rules themselves changed almost nothing in aggregate. Some are stricter than before: hearing notice is now checked in House committees, and House bills that never get a hearing now have a deadline. Others are more lenient: vote records are only required once a committee has actually voted on or reported a bill, and a posting is only judged late if it's 3 or more days overdue. On bills both versions checked, these changes cancel out.

## "Procedural transits"?
Many House and Senate committee bills never get a hearing or a vote. The old tool listed them as Provisional and counted them in the headline percentage. The new one doesn't create a segment for them at all, which removes 3,309 of these entries.

I think that's the right call. A bill nobody has acted on doesn't show that a committee followed the rules, and letting thousands of them count as passing hid the results that do. The new Tracker still marks 2,473 segments Provisional, almost all because the data needed to judge them isn't available yet.

## Why the new number is more defensible
- **Every result includes provenance:** Each segment links to the evidence it relied on and the rule text it applied. If a classification is wrong, you can see exactly why. This has made it easier to scrutinize and fix edge cases.
- **Complete coverage:** Every bill before every tracked committee is checked, beyond what was discoverable before, due to better bill collecting algorithms developed in the past few weeks.
- **Fixes apply retroactively:** When I correct a rule, past classifications are re-evaluated for every bill, making sure rules are applied evenly. Every bill has now passed through the updated ruleset.

I should have caught the stale rows and the mislabeled bills sooner, and I take any responsibility for the confusion the old number caused. The current 37.6% figure is lower, not because the Legislature's public facing record changed overnight, but I believe it is a more defensible, representative reading of overall compliance.

As always, if you find a classification that looks wrong, please [report it](/contact).
