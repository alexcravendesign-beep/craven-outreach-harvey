# Signal Playbook — Craven Cooling

A cold email earns a reply when its first line is a true, specific observation
about *this* venue, said the way a tradesperson would say it over the bar.
Scout attaches the venue's signals; this playbook turns each into that first
line. British English throughout. No exclamation marks.

## The core rule

The signal is the reason we are writing today. Say it plainly in the first
sentence, as something anyone could have noticed, never as something we ran a
scan for. "Saw you're waiting on your first hygiene inspection" is fine. "Our
system flagged that your FHRS status is AwaitingInspection" is not. Never claim
more than the signal proves, never invent a detail, never mention the data
source by name. A wrong specific is worse than a right generic.

Every email names the venue (`{{company}}`), not a person: we write to the
shared mailbox. Second person plural is natural ("you're", "your kitchen").

## Which signal leads

When a venue has several, the first line uses the first match in this order:

1. **FHRS_AWAITING_INSPECTION** — a new venue, fitting out now.
2. **CH_NEW_INCORPORATION_DAYS** under 120 — same story from Companies House.
3. **CH_OFFICER_CHANGE_DAYS** under 180 — new owner or manager, refit likely.
4. **FHRS_STRUCTURAL_SCORE** of 10 or worse — condition of equipment.
5. **FHRS_INSPECTION_AGE_DAYS** over 540 — inspection likely due.
6. **MENU_EQUIPMENT_CLASS** — what their menu says they run.
7. **VENUE_CATEGORY** alone — the kit a venue of that kind lives on.

Everything else (ENERGY_CLAIM, FHRS_RATING of 5, FHRS_BUSINESS_TYPE) colours
the second sentence or the follow-up; it does not lead.

## Signal → first line

### FHRS_AWAITING_INSPECTION (new venue)
The fit-out window: kit is being chosen this month.
- **Angle:** get the chilled storage right before the inspector sees it.
- **Good:** "Looks like {{company}} is opening soon — are you sorted for
  fridges and the bottle cooler yet, or still pricing it up?"
- **Avoid:** congratulations, "exciting times", anything about the inspection
  being scary.

### CH_NEW_INCORPORATION_DAYS / CH_OFFICER_CHANGE_DAYS (new company or owner)
- **Angle:** a new owner usually inherits someone else's fridges.
- **Good:** "New hands on {{company}} by the look of it. If you've inherited
  the kitchen kit, worth knowing what it's costing to run before summer."
- **Never** name the director, the filing, or Companies House. We say "by the
  look of it", not "I see you were appointed on the 3rd".

### FHRS_STRUCTURAL_SCORE (condition of the building and equipment)
A poor structural score is, more often than not, chilled storage in poor nick.
- **Angle:** condition and temperature, not hygiene. Never mention the rating.
- **Good:** "Older fridges tend to be the thing that lets a kitchen down on the
  day — if yours are struggling to hold temperature, I can tell you what a
  swap costs without a sales visit."
- **Avoid:** the word "rating", the score, anything that reads as a dig.

### FHRS_INSPECTION_AGE_DAYS (inspection likely due)
- **Angle:** temperature records and reliability, offered as help.
- **Good:** "If you're due a visit this year, the two things that get asked
  about are temperature logs and whether the fridges actually hold 5°C. Happy
  to send a one-page checklist."
- Only when the age is over about 18 months; otherwise it is not a signal.

### MENU_EQUIPMENT_CLASS (what the menu names)
The main copy angle because it names the product and lets us quote a price.
| On the menu or site | They run | Lead with |
|---|---|---|
| gelato, ice cream, sorbet | display freezer | the display they sell from |
| bottle bar, craft cans, wine by the glass | bottle coolers, back-bar | glass-door bottle cooler, price inline |
| deli counter, fresh fish, cured meats | serve-over | a serve-over sized to the counter |
| cakes, patisserie, bakes | display chiller | an upright cake display |
| real ale, cask | cellar cooling | cellar cooler and remote |
| takeaway, fast food | undercounter and prep | prep counter with a price |
| farm shop, butcher | serve-over and multideck | the open-front multideck |
- **Good:** "Your cake counter's doing the selling for you — if the display
  chiller ever wobbles, a Tefcold upright that size is about £X delivered."
- The price must come from `product_knowledge.md`; never guess one.

### VENUE_CATEGORY (nothing more specific)
Fall back to the kit a venue of that type depends on, phrased as a question.
- **Good (pub):** "Quick one for {{company}}: how are the bottle coolers
  holding up behind the bar this summer?"
- **Good (cafe):** "How's the cake display coping — holding temperature on a
  warm afternoon?"

### ENERGY_CLAIM (they talk about energy or sustainability)
Second sentence only. "Running on R290 cuts a bottle cooler's electricity by
about a third" — only with the figure from `product_knowledge.md`.

### FHRS_RATING of 5
Second sentence, as respect: "You clearly run a tight kitchen." Never lead on
it and never mention lower ratings at all.

## Signals we do not use in copy
- **ROLE_MAILBOX, INDEPENDENT_VENUE, CH_INCORPORATED** are filters and
  compliance facts, not angles. They never appear in an email.
- **FHRS_HYGIENE_SCORE / FHRS_MANAGEMENT_SCORE** inform whether to send; they
  are never quoted or alluded to. Food safety is their business, not our pitch.

## Honesty lines
- If a prospect asks whether this is written by an AI, the answer is yes,
  straight away, every time. Alex Craven is Craven Cooling's openly AI
  assistant; a named person takes the call.
- One email, one observation, one question. Under 75 words. No links in the
  first email; the second may carry one link to a sector page.
- Stop on reply. An opt-out is honoured permanently, everywhere, immediately.
