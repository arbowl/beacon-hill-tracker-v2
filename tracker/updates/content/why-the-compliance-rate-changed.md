+++
title = "Why the Compliance Rate Dropped from 52% to 37.6%"
published = 2026-11-22
category = "analysis"
summary = "The new algorithm reports a lower compliance rate than the old site. Here's where the difference comes from, what I got wrong before, and why the new number is the better one."
+++

_Posted on November 22, 2026 at 9:12 AM._

_Updated at November 22, 2026 at 1:02 PM._

When the new Tracker launched, the headline compliance rate fell from about 52% on the old site to 37.6%. That's a big swing, and anyone who cited the old number deserves an explanation.

The short version: The change reflects improvements and refinements in measurement, not a sudden change in legislative behavior. The headline compliance number is derived from subtracting Non-Compliant [bill segments](/methodology) from the total. In other words, the primary compliance number is a ceiling; computed compliance is only ever expected to sustain or fall below that number. The old number included House and Senate Committee bill segments that had no hearing and no vote, which meant they would never progress past Provisional to Compliant or Non-Compliant. Since there are no applicable rules to comply _with_ in those instances, it resulted in thousands of "stale" bill segments that inflated the specific way the Tracker presents total compliance. By removing them, the headline compliance percentage gives a more defensible and representative snapshot of what is changing each day in the Legislature's public record.

I also added some routine spot improvements to the algorithm, noted per usual in the [changelog](/changes); these changes are smaller but still worth highlighting.

## How the rate is calculated
Both the old-site and new-site calculation use the same formula for the headline compliance percentage: the share of bill segments that are **not** Non-Compliant. A "segment" is one bill as handled by one committee. Compliant segments count toward the rate, as do Provisional ones (meaning the deadline hasn't passed or there isn't enough data to judge yet).

The key here is that anything the tool can't judge counts in a committee's favor, so when there are thousands of bill segments that can't be judged, it creates a permanent compliance "floor" that never resolves and was never bound by the rules I set out to measure.

## Where the difference comes from
I compared the old and new algorithms bill by bill on the same day, November 20, 2026. Each row below changes one thing in order:

| Step | Rate |
|---|---|
| Old site, as displayed | ~52.0% |
| Removing bill segments with no hearing and no vote | 26.8% |
| Correcting bills the old tool mislabeled as Compliant | 17.5% |
| Applying the new rules to bills both versions checked | 17.5% |
| Adding joint committee bills the old tool never listed | 22.5% |
| Adding every House and Senate committee bill | 37.6% |

1. **Procedural transits:** Many bill segments exist only for a short time in a House or Senate committe's tenure. These segments contain no hearing, no vote, and are often quick turnarounds. These contain no true compliance content to analyze, so pruning these removes many Provisional bill segments from the count.
2. **Mislabeled bills:** The old tool marked 703 joint committee bills Compliant even though the data showed they were reported out after their deadline. Fixing those lowers the rate by 9.3 points.
3. **Coverage:** The old tool missed some bill pages for House and Senate committees. The new one checks every bill: 2,320 House and Senate segments, nearly all of them passing or still pending. It also picks up 1,112 joint committee segments the old tool never listed. Together these *raise* the rate by about 20 points.

The new Tracker rules themselves changed almost nothing in aggregate. On a bill-by-bill basis, very little actually changed.

## "Procedural transits"?
Many House and Senate committee bills never get a hearing or a vote. The old tool listed them as Provisional and counted them in the headline percentage. The new one doesn't create a segment for them at all, which removes 3,309 of these entries.

I think that's the correct method going forward because bills nobody has acted on don't show whether a committee followed the rules; letting thousands of them count as passing hid the results that do. The new Tracker still marks 2,473 segments Provisional in cases where results are actually pending and resolvable.

## Why the new number is more defensible
- **Compliance calculations will actually resolve:** The new system places a renewed empahsis on *tracking*; bill segments which linger forever with no possible resolution have no content to track.
- **Individual bills are still tracked the same:** Most of the project is devoted to tracking Joint Rules across Joint Committees, and these classifications fundamentally haven't changed.
- **Every result includes provenance:** Each bill now has an "audit trail" to show exactly how the Tracker arrived at a designation. The hope is that this will ensure confidence in individual results and give a clear path to fixing edge cases where they are found. 

I take any responsibility for the confusion the number change may cause. The new algorithm has been a work in progress for some time, and I released it as soon as I had confidence in the new results. The current 37.6% figure is lower, not because the Legislature's public facing record changed overnight, but I believe it is a more defensible, representative reading of overall compliance.

As always, if you find a classification that looks wrong, please [report it](/contact).
