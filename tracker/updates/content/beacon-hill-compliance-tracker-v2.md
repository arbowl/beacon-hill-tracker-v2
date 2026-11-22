+++
title = "Beacon Hill Compliance Tracker v2"
published = 2026-11-21
category = "announcement"
summary = "Why I rebuilt the Tracker, what changed in how it counts, and what stays the same."
+++

On November 21, 2026, I released a major update for the Beacon Hill Compliance Tracker: a new algorithm and a new website.

## Why?
1. **Scaling:** The original tool was meant to produce a single report a single time for civic advocates to snapshot the Legislature's site with passable accuracy. After a year of edits, it was becoming increasingly difficult to find bugs and add improvements.
2. **Usability:** The old site was unoptimized because I didn't know what kind of data folks would be most interested in. Users should find the new site significantly more responsive.
3. **Familiarity:** When I started, the Joint Rules and their markers on the official site were fairly new. I have a much better idea of how to search for and evaluate compliance.

## What's different?
- Each bill now carries an in-depth audit trail connecting the evidence and the rule text to the final classification.
- Old accounts have been retired; their only feature was saving views. Saving views is now a default feature, and accounts may download datasets, generate API keys, and create watchlists.
- Historical data is stored separately from classifications, so improvements to the ruleset can be retroactively applied to correct misclassifications in old data.

## What's NOT different?
- The core mission of the Tracker as a non-partisan data source remains the same.
- Changes to the algorithm will continue to be logged and explained.
- You can always [report](/contact) bugs and inaccuracies; each one goes into the standard testing and revision workflow.
