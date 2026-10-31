<!-- TEMPLATE-SAMPLE: fictional Alex Example data. Delete this line when you put your own data in this file. -->
# Interview Story Bank — Alex Example (SAMPLE)

*The stories behind the bullets, including **INTERVIEW ONLY** details that are true but stay off paper. Format: **Situation → What I did → Result → Line to land it.** About 90 seconds each. Bullet IDs point to `B_Bullet_Catalog.md`.*

## Quick map: question → story
| If they ask about... | Use story |
|---|---|
| Performance, frontend architecture | 1. The 4-second scheduling page |
| Risky changes, databases, reliability | 2. 18 million rows, no downtime |
| Technical leadership, mentoring | 3. Leading without reports |

## 1. The 4-second scheduling page (FE-01)
- **Situation:** patients dropped off the booking flow; the page took over 4 seconds to load on phones.
- **What I did:** split the bundle, moved availability lookups to the booking API, rebuilt the views in React and TypeScript.
- **Result:** largest contentful paint went from 4.1 to 1.6 seconds for about 40,000 weekly users.
- **Line:** "Most of the win was not loading what the patient couldn't see yet."

## 2. 18 million rows, no downtime (BE-02)
- **Situation:** billing ran on an aging MySQL instance that blocked new features.
- **What I did:** dual writes, a nightly row-count and checksum diff, then a read switch behind a flag.
- **Result:** six weeks, 18 million rows moved, no customer-facing downtime.
- **Line:** "We made the cutover boring on purpose."

## 3. Leading without reports (LEAD-01)
- **Situation:** a new squad of four, three of them in their first engineering job.
- **What I did:** design-doc template, pairing rotations, code review with a written "why."
- **Result:** all three shipped production features within their first two months.
- **Line:** "I review for the next pull request, not just this one."

## Honest gap answers
- **Kubernetes:** "Not in production. We run containers on AWS ECS with Terraform; the concepts carry over, and I'd ramp on the cluster side."
- **People management:** "No direct reports. I'm a tech lead: planning, design reviews, mentoring. Hiring and reviews sit with my manager."
