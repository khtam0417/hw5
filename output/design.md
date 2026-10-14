# Dashboard design — Campus Customs operations desk

The dashboard in `frontend/` (React, Vite and TypeScript) is where a person watches the five agents work and approves payments. It uses only the backend's Problem 7 routes at http://localhost:8000, and every number and status it shows comes from them. The colors follow the course's black-and-pink style.

## Layout

The desk follows a person's workflow from left to right: pick a ticket, watch the agents work on it, then decide on payments.

| Zone | What it holds | Why it is there |
|---|---|---|
| **Header** (every screen) | The brand, then chips for the shop date (`desk.date_today`), the database (the working database or an isolated test copy), the agents (Claude Opus 5.5, or simulated), the session's run label when the backend sets one (for example "Problem 9 full run"), and the connection ("Live"). On the right, the **checking balance and the number of requests to approve**, linking to the approvals panel. | The facts that change how everything else should be read are always visible: which day it is for the shop, which data this is, and who the agents are. Money is never more than a glance away. |
| **Tickets** (left) | All three tickets with id, type, subject, requester, item, the backend's status badge, an "Agents working" badge during a run, a failed-run badge, and the number of requests waiting for approval. | Every ticket's state is visible at once, and choosing one is a single click. |
| **Agent activity** (center, widest) | The selected ticket's details, the **Start agent team** button with an optional **note for the Boss** (for a decision only a person can make), run tabs, the roster of five agents, and, after a run, its outcome, what each agent did and what a person must do next. Below that is the live activity feed. | Activity has the most text, so it gets the most width. The summary sits above the feed, because once a run ends the summary is what a person needs first. |
| **Approvals & cash** (right) | The checking balance card, the approver's name, the requests waiting for a person, and recently decided requests. | Approving moves money, so it has its own column that stays in view (it is sticky) while the feed scrolls. |

**Responsive layout.**
- **1280 px and wider:** three columns.
- **900–1279 px:** tickets go across the top of the main column, activity sits below them, and approvals keep a column on the right.
- **Below 900 px:** one column, ordered tickets, then **approvals and cash**, then activity. The money panel comes before the long feed, so a request never sits far below the fold.
- **Phones:** the header scrolls away instead of covering the page, the agents take two columns, and each event's time sits above it.

There is no horizontal scrolling at 375, 768, 1024 or 1440 px wide.

## Agent identities

Each agent has its own color, icon and name. The color is used for its card border and glow, its chips, its timeline rail in the feed, its summary card, and the "Asked by" chip on payment requests. Color is never the only signal: the icon and the name always go with it.

| Agent | Color | Icon | Why |
|---|---|---|---|
| Boss | Hot pink `#ff4fa3` | Crown | The brand's own pink for the lead. The Boss starts and ends every run and records the decision. |
| Inventory | Coral `#ff8a5c` | Box | Warm and physical: stock on shelves and vendor shipments. |
| Accounting | Lavender `#b69cff` | Stacked coins | Cool and calm for money, and set clearly apart from the pinks. |
| Facilities | Gold `#ffc85c` | Building | Gold for the property and its rent. It is the brightest accent, so the rare rent runs stand out. |
| Customer Service | Blush `#ffb3d9` | Speech bubble | A soft pink for messages. They are always drafts, never sent. |

The five colors sit around the pink end of the spectrum, from coral to lavender. That keeps the page black-and-pink while letting each agent be told apart at a glance.

The **roster** shows each agent's state in the run on display, worked out from the audit records:
- **Working now:** a glowing border and a pulsing dot.
- **Waiting on a teammate:** the agent asked someone and is waiting for the answer.
- **Done**, with the number of tools it used.
- **Stopped:** the run ended before the agent finished.
- **Not involved:** a dashed card.

## Live activity

- **Polling.** The desk asks the backend for new audit records every second while a run is going. When idle it checks every 5 seconds, or every 15 when the tab is hidden.
- **No duplicates.** It only asks for records after the last index it has seen, and stores records by their index in the append-only file. A repeated page can't add a record twice.
- **No mixing of tickets.** Records are grouped by run id, and each run belongs to one ticket. Runs on other databases are left out, such as test copies recorded in the same audit file. Runs from before a database reset are hidden too.
- **The timeline.** Each step is one row, indented by delegation depth, with the agent's color on the rail. It shows:
  - who asked whom, with the request as a message bubble;
  - each tool call with its arguments and a one-line summary of the actual result, for example "0 on hand · Aisle B";
  - refusals, with a dashed rail and a "refused" chip;
  - each report;
  - the end of the run.

  "Input and result" opens the raw record.
- **Following the run.** The feed follows new events unless the person scrolls up to read. A "Facilities is working…" line shows who is busy.
- **After a run.** An outcome banner, a **What each agent did** card per agent, and **Next for a person** appear:
  - The agent card holds the agent's report, its key actions taken from real tool results (approval requests, drafts, ticket updates and refusals), and its tools.
  - **Next for a person** comes from the Boss's `needs_human`, with a link to the approval panel.

## Resolved tickets and the other states

The badge always shows the ticket's status as the backend stores it. On an earlier run of a ticket, the banner says that a later run followed and gives the ticket's current status, so it never contradicts it. The Resolved banner says the shop's own work on the ticket is complete. When the Boss lists steps that happen outside the system, such as sending a draft, the banner points to them instead of claiming that nothing is left to do. Resolved never implies delivery or a customer's acceptance. The **Resolved** style is only used when the backend reports the ticket resolved (`resolved: true`, status `resolved`). A finished run is not a resolved ticket: its banner reads "Run finished, ticket not resolved" and names the current status.

| State | Style |
|---|---|
| **Resolved** | The only **solid pink** badge (with a check). The card gets a pink tint and a large check watermark, and the outcome banner is a full pink "Resolved". |
| Waiting for approval | Pink outline, clock icon. |
| Waiting for vendor | **Dashed** pink outline, truck icon, and the hint "Nothing has shipped". |
| Waiting for customer | **Dotted** outline, speech icon. |
| Needs a person | White outline, person icon. |
| Open | Grey outline, circle. |
| **Run failed / stopped / interrupted** | A **dashed red** badge with an X, shown next to the ticket's own status, and a dashed red card border. The outcome banner gives the reason and says the ticket "stays Open and is not marked resolved". |

Each state differs in line style, icon and words as well as color, so they can't be confused even in grayscale.

## Cash display

- **One big number:** the checking balance, dated with the shop date. The same balance is in the header on every screen.
- **What is already spoken for:**
  - a striped pink meter shows how much of the balance pending requests would use;
  - two figures show "Waiting for approval" and "Left if all are approved";
  - if pending requests add up to more than checking holds, the figure turns red and a warning explains that not all of them can be paid.
- **A visible decrease:** after a payment, the balance counts down to its new value and a chip reads **"Paid out −$840.00"**. A change made elsewhere, for example by `approve.py`, reads "Balance changed".
- **A standing reminder:** "Only an approved payment lowers this balance. Sales revenue is never added."

## Approving a payment or purchase

- **What a request card shows:** the kind ("Vendor purchase" for an invoice, or "Rent payment"), what is paid, the amount in large type, the **purpose** the agent wrote, the due date, the agent that asked, and a link to its ticket.
- **The approver's name is required.** It is recorded on the payment and remembered in this browser. The backend refuses agent and system names.
- **A two-step approval.** "Approve & pay $840.00" opens a confirmation that shows the balance before and after ("Checking goes from $3,400.00 to $2,560.00"), and **Confirm payment** calls the approval route. Cancel or Escape backs out.
- **No repeated clicks.**
  - While a request is processing, its button reads "Paying…" and is disabled, and every other request's buttons are locked.
  - A second click before the first finishes is ignored. Three clicks in the same instant sent one request.
- **Errors in place.** A refusal from the backend appears inside the card as an alert, and the request stays pending. Refusals include an agent name as approver, not enough cash, and a request that was already paid.
- **After a payment:**
  - a confirmation names the payment id and the new balance;
  - the balance and requests refresh, and the request moves to "Recently decided";
  - the ticket suggests running the agent team again, because the agents don't resume on their own.
- **Requests that arrive mid-run** glow with a "New" badge and are announced to screen readers.

## Creative choices, and why they help

- **Hazard-tape banner and chips for simulated agents or an isolated copy.** A sandbox can't be mistaken for the real shop.
- **The delegation timeline.** Indentation and the agents' colors show who asked whom and how deep, mirroring the two-level delegation limit.
- **"What each agent did" from real tool results,** not only the agents' own words. The person sees concrete outcomes such as "Saved draft shipment request #2 … (not sent)".
- **"Next for a person."** The Boss's open items become a short to-do list, linked to the approvals.
- **A two-step approval with a preview of the balance after the payment.** A payment can't be undone, so the person sees its effect before confirming.
- **The live counter and "Paid out" chip.** The decrease is something you see happen, not a number that silently changes.
- **The check watermark on resolved tickets.** Finished work stands out in the list at a glance.

## Accessibility

- **Contrast (WCAG AA needs 4.5:1).**
  - Body text is 16–18:1, muted text 7.8:1, and the faintest helper text 5.4:1.
  - Pink accents are 5.8:1, and black on the pink buttons and Resolved badge is 6.4:1.
  - The agent colors are 5.8–11.5:1.
- **Keyboard.**
  - Every control is a real button, link or input, with a visible pink focus ring.
  - The first Tab stop is "Skip to approvals and cash".
  - Tickets select with Enter or Space, Escape cancels a payment confirmation, and the feed can be focused to scroll it.
- **Screen readers.**
  - Icons are hidden, and every badge has words.
  - The selected ticket is `aria-current`, and run tabs are `aria-pressed`.
  - Busy controls are `aria-busy`.
  - Runs that end, new payment requests and balance changes are announced in polite live regions.
- **Motion.** Animations stop when the system asks for reduced motion.

## Loading, empty and error states

- **Loading:** shimmering placeholders for the tickets, the cash card and the requests.
- **Before a run:** "No agent activity yet" explains what will happen, and "Starting the agent team…" covers the moment before the first record arrives.
- **No requests:** "Nothing to approve" explains that nothing is paid until a person approves.
- **Backend unreachable:** a red banner gives the command to start it and offers a retry; the desk keeps retrying on its own. A partial failure shows a single line and retries.
