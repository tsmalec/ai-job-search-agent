<!-- TEMPLATE-SAMPLE: fictional Alex Example data. Delete this line when you put your own data in this file. -->
# B — Bullet Catalog (THE MASTER)

*SAMPLE DATA for an invented candidate, Alex Example, a full-stack software engineer. Replace it with your own (START_HERE.md walks an agent through it). Every resume, cover letter and form answer is built from this file, never from an old resume. New confirmed facts go straight in here. Rules in `project/A_Rules.md` always apply.*

**How to shop**
1. Build the shopping list: the JD's responsibilities and keywords, in order.
2. Run `scripts/shop.py`. Pick the items that match.
3. Use `full` for a two-page draft and `short` when fitting one page. Swap in the JD's exact words **only where `facts` support it**.
4. Each item's `facts` line is the proof and the limits. Never write past it.

**Format:** `### ID · name`, then `role:`, `tags:`, `full:`, `short:`, `facts:` (and `interview:` / `context:` when they apply). IDs: `AISLE-NN`, next free number in that aisle.

**Role lines:** every item names the role(s) it was true under, using the codes in `config.json` → `resume.role_codes`: `SSE` Senior Software Engineer, `SE` Software Engineer, `WD` Web Developer, `summary` (openers), `none` (never under a job). `guard.py` blocks a bullet under a job its role line doesn't list.

**Roles (for headers):**
- Senior Software Engineer | Globex Health, Denver, CO | April 2022 - Present
- Software Engineer | Initech, Boulder, CO | July 2019 - March 2022
- Web Developer | Vandelay Industries, Denver, CO | June 2018 - June 2019
- Education: Bachelor of Science, Computer Science | Example State University, Fort Collins, CO | 2018
- Header: ALEX EXAMPLE / Denver, CO 80202 | 555-010-0100 | alex@example.com | github.com/alex-example

**Years wording:** total experience is "eight years building web applications" (Jun 2018 on). Tech lead work gets its own number: "two years as tech lead" (Apr 2024 on). Alex has never managed people: say "tech lead" or "mentored," never "managed a team."

---

## Aisle 1 · Summary openers (SUM)

### SUM-01 · Full-stack engineer
role: summary
tags: full stack, software engineer, web applications, TypeScript, React, Node.js, PostgreSQL, AWS
full: Full-stack software engineer with eight years building web applications in TypeScript, React, and Node.js. Rebuilt Globex Health's patient scheduling app and the API behind it, cutting page load from 4.1 to 1.6 seconds, and leads a four-engineer squad with a focus on testing, CI/CD, and AWS.

### SUM-02 · Backend and platform
role: summary
tags: backend, APIs, platform, PostgreSQL, AWS, infrastructure, reliability
full: Backend-leaning full-stack engineer who designs APIs and data migrations that hold up in production. Brings eight years across Node.js, Python, and PostgreSQL on AWS, including a zero-downtime migration of 18 million billing rows and a deploy pipeline that went from 45 minutes to 12.

---

## Aisle 2 · Frontend (FE)

### FE-01 · Scheduling app rebuild
role: SSE
tags: React, TypeScript, frontend, performance, web vitals, accessibility
full: Rebuilt the patient scheduling web app in React and TypeScript, cutting largest contentful paint from 4.1 to 1.6 seconds for about 40,000 weekly users.
short: Rebuilt the patient scheduling app in React and TypeScript, cutting page load from 4.1 to 1.6 seconds.
facts: LCP p75 from the real-user monitoring dashboard, before (Q1 2023) vs after (Q4 2023). ~40,000 weekly active users. Alex led the frontend; a designer and one other engineer worked on it.

### FE-02 · Component library
role: SSE
tags: design system, component library, React, Storybook, reuse, frontend
full: Built a shared React component library in Storybook that 5 product teams use, replacing three copies of the same form and table code.
short: Built a shared React component library used by 5 product teams.
facts: 5 teams adopted it by mid-2024. About 60 components. Alex started it and owns review; others contribute.

---

## Aisle 3 · Backend (BE)

### BE-01 · Booking API
role: SSE
tags: Node.js, TypeScript, REST, GraphQL, APIs, Redis, caching, performance
full: Designed the Node.js and TypeScript booking API (REST and GraphQL) that serves about 1.2 million requests a day, and cut p95 latency from 220 ms to 90 ms with Redis caching.
short: Built the Node.js booking API (1.2M requests/day) and cut p95 latency from 220 to 90 ms.
facts: Request volume and latency from the APM dashboard, 2024. Caching layer and API design by Alex.

### BE-02 · Zero-downtime database migration
role: SSE
tags: PostgreSQL, MySQL, data migration, databases, zero downtime, SQL
full: Led the migration of the billing service from MySQL to PostgreSQL with dual writes and no downtime, moving 18 million rows over six weeks.
short: Migrated 18M billing rows from MySQL to PostgreSQL with zero downtime.
facts: 18 million rows; six-week dual-write window in 2023; no customer-facing downtime per the incident log.

### BE-03 · Invoice processing services
role: SE
tags: Python, FastAPI, microservices, event-driven, batch to real-time, APIs
full: Built Python (FastAPI) services that replaced a nightly invoice batch job, so customers saw new invoices within minutes instead of the next day.
short: Replaced a nightly invoice batch with Python services, from next-day to minutes.
facts: Initech, 2020-2021. Queue-based; "minutes" = under 5 minutes at the 95th percentile.

---

## Aisle 4 · Infrastructure & quality (OPS)

### OPS-01 · CI/CD and infrastructure as code
role: SSE, SE
tags: CI/CD, GitHub Actions, Terraform, AWS, ECS, containers, DevOps, deployment
full: Moved deploys to GitHub Actions and Terraform on AWS ECS, cutting deploy time from 45 minutes to 12 and taking the team from weekly releases to about 20 deploys a week.
short: Cut deploy time from 45 to 12 minutes with GitHub Actions and Terraform on AWS.
facts: Started at Initech (container builds), finished at Globex (Terraform + ECS, 2023). Deploy counts from GitHub Actions history.

### OPS-02 · Testing and incident reduction
role: SSE
tags: testing, Jest, Playwright, test automation, quality, reliability, incidents
full: Raised automated test coverage from 38% to 81% with Jest and Playwright, and production incidents fell from 14 to 5 a quarter.
short: Raised test coverage from 38% to 81%; production incidents fell from 14 to 5 a quarter.
facts: Coverage from the CI report; incidents from the on-call log, 2023 vs 2024. Other changes contributed too: never claim Alex alone cut incidents.

---

## Aisle 5 · Leadership (LEAD)

### LEAD-01 · Tech lead
role: SSE
tags: tech lead, technical leadership, code review, design docs, sprint planning, mentoring
full: Tech lead for a four-engineer squad since 2024, running sprint planning, design reviews, and code review, and mentoring three junior engineers.
short: Tech lead for a four-engineer squad; mentored three junior engineers.
facts: Tech lead since April 2024. No direct reports: the engineering manager owns hiring and reviews. Never "managed."

---

## Aisle 6 · Early career (WEB)

### WEB-01 · E-commerce storefront
role: WD
tags: JavaScript, PHP, e-commerce, frontend, web development
full: Shipped more than 30 storefront features in JavaScript and PHP for an e-commerce site with about 2 million monthly visitors.
short: Shipped 30+ storefront features for a site with 2M monthly visitors.
facts: Vandelay Industries, 2018-2019. Feature count from the release notes.

---

## Skills vocabulary (pick in JD order; only what's true)
**Languages:** TypeScript; JavaScript; Python; SQL; PHP.
**Frameworks:** React; Node.js; Express; GraphQL; FastAPI; Storybook.
**Data & infrastructure:** PostgreSQL; MySQL; Redis; AWS (ECS, Lambda, S3); Terraform; GitHub Actions.
**Testing:** Jest; Playwright.
**Capabilities (approved labels):** Full-Stack Development; API Design; Database Migration; CI/CD; Test Automation; Performance Optimization; Technical Leadership; Mentoring.

---

## Not for paper (interview-only extras, no bullet)
- **INTERVIEW ONLY:** covered on-call for the payments team for one month in 2024 while they hired.
- **INTERVIEW ONLY:** once fit a whole resume on one page without cutting a single bullet. `onepage.py` still doesn't believe it. The builder was here. — C
