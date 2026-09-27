# DESIGN.md: how Petasos looks and behaves

Status: PROPOSED, version 0.2 (2026-09-26: aligned with D-015 to D-017)

This document is the product and UX design for two things: the demo web app ("Hermes Helpdesk", a fake customer support desk) and the static site at petasos.io. It is written for the coding agents that will build both, and for Elias, who will ratify it. The patterns in this document are drawn from a private production system called Talaria. Nothing else in this file names it again: every rule below is re-explained from scratch, in its own words.

A few words used often below, defined once here. A **tier** is a label from L1 (harmless) to L5 (dangerous) that says how risky an action is. A **grant** is a staged, single-use permission slip for one risky action, waiting for a person to approve or abort it. A **verb** is the short plain name of what a grant would do, such as "send an email." A **canary** is a hidden marker planted in private data. It is a tripwire, not a general leak-prevention system: it catches an attempt to read that exact data back out; keeping risky actions from happening in the first place is the job of the tier rules. A **ledger** is a list of everything that happened, written so that changing an old entry breaks the chain from that entry forward.

## 1. Design goals

The whole point of the demo app is that a person who has never written code can look at it for two minutes and understand exactly how the safety system works: what it will do on its own, what it stops to ask about, and how asking works. Ease comes before power everywhere: no field where someone must type a command, an id, or a secret phrase, and no screen that requires reading code or logs to understand what happened. Every design decision below is judged by one question: does this make the safety model easier to see, or does it just make the app more capable? When those two goals conflict, ease wins, and the tradeoff gets written down instead of hidden.

## 2. Screens and flows: the demo app

### 2.1 Home (tickets)

What the person sees: above the ticket list, one highlighted button leads the screen: "Ask for a $42 refund." Tapping it has the simulated agent propose that exact refund, then walks through why it waits, the approval card built from the request, the press-and-hold approval, the outcome sentence, and the new ledger row, before returning here, so a person who taps nothing else still sees the whole safety story once. Below that sits a plain list of fake support tickets (a subject line, a customer name, a status word). At the top of every screen, always, sits a thin bar with the app name, a quiet pending-approvals pill (see section 3), and a three-way switcher labeled "You are:" with the options **Owner**, **Agent**, **Visitor**. Switching it changes what the rest of the app can see and do, live, without reloading.

What the person does: taps a ticket to open it and read its notes; taps a switcher option to change identity; taps the pending pill to go to the approvals inbox.

Empty state copy: "No tickets right now. Check back after the next hourly reset."

### 2.2 Approvals inbox

What the person sees: a list of pending approval cards, newest first. Each card has a colored strip down its left edge, a verb in plain words at the top of the card ("Send this email to the customer"), and below that, as ordinary text, the summary of what will happen. Under the summary sits one button: "Hold to approve." Below the button, small helper text reads: "Slide your finger off to cancel."

Colors: an L4 card (something reversible-ish but external, like sending an email) has an amber strip and an amber-tinted header. An L5 card (something that spends money, deletes something, or cannot be undone) has a crimson strip, a crimson-tinted header, and the word "Cannot be undone" printed under the verb.

What the person does: presses and holds the button. A fill sweeps across the button as they hold it: 1.2 seconds for an L4 card, 2.5 seconds for an L5 card. If they lift their finger or slide it off the button before the fill finishes, nothing happens and the fill resets, no penalty, no message beyond the button quietly resetting. If they hold to the end and release while still inside the button, the action is approved at that instant.

Each card also carries a small quiet "Abort" text link, grey, not crimson: aborting a staged action is the safe choice, so it never looks alarming.

Refusal states for the inbox itself (not the individual cards): if the app cannot find out what is waiting (for example, a Visitor tries to open it, or a network call fails), the screen shows: "We could not check what is waiting for you. Try again in a moment." It never shows an empty list in that case, because an empty list would look identical to "nothing is pending," which might be false.

### 2.3 Outcome

After a hold-to-approve finishes, or after a card is aborted, the person sees one plain sentence, never a status code and never a technical word. Examples: "Email sent to the customer." "Refund of $42 issued." "Ticket deleted." "This was aborted. Nothing happened." If the approval could not go through because the underlying ticket changed after it was staged, the sentence reads: "This could not be approved because the request changed after it was staged. Nothing happened, and it is safe to ask again."

### 2.4 Ledger

What the person sees: a plain table of everything that has happened, most recent first: a time, who did it (Owner, Agent, or Visitor), a plain description of the action, and a tier chip. Every description is generated by the system itself; no row ever holds text a visitor typed. At the top sits one button: "Verify chain."

What the person does: taps "Verify chain." If every row is untouched, the app shows: "Every row checks out. The chain is internally consistent." If a row was edited by hand (this is demonstrated live in the demo), the app shows: "Row 14 does not match what it should. Something was changed after it was written." It always names the first row that fails, never just "something is wrong somewhere."

### 2.5 Canary demo

What the person sees: a single button, "Try to peek at the hidden ticket," and a short line above it explaining what it does: "There is one ticket this system is never supposed to show anyone. Press the button to see what happens when someone tries."

What the person does: taps the button. The result appears instantly: "Blocked. This ticket is never shown to anyone, no matter who is asking, including me. The attempt was written to the ledger below." A link jumps to the new ledger row.

### 2.6 The "You are" switcher

Three options, always visible, always in the topbar: **Owner** (can see and approve everything), **Agent** (can read tickets and ask for risky actions, but cannot approve them), **Visitor** (can only read tickets, and only the ones a stranger should see). Switching identity is instant and never asks for a password. It exists so a reviewer can feel the difference between the three roles in seconds rather than reading about it. Switching to Owner in this public demo simulates approval; it is not proof a human approved, since anyone can flip the switch. A real deployment approves through protected credentials that only the actual owner holds, and a proposer credential can never approve its own request.

## 3. Binding UI rules

| Rule | One-line reason |
|---|---|
| Tier is never shown with color alone; it is always the left strip, the tinted header, and a text chip together | So the tier is still clear to someone who cannot rely on color to tell hues apart |
| Crimson is used only for an L5 card or for a failure, never anywhere else | So crimson always means real danger and never becomes decoration |
| The pending-approvals pill lives in the top bar on every screen and is never tucked inside a menu | A badge behind a closed menu cannot be noticed, and noticing is the whole point |
| A refused or unknown read is never shown as "nothing waiting"; the app only ever shows one of three states: unknown, a number waiting, or nothing waiting | Claiming "nothing waiting" when the app actually could not check would be a false promise of safety |
| The verb sits above the summary on every approval card, and the summary always renders as plain text, never as formatted text | The verb is the one fact the server itself guarantees is true; formatting the summary could make something look more or less important than it is |
| No screen ever shows a string that could be copied and pasted somewhere else to approve an action | A copyable approval is the same as a stolen key |
| The hold duration used in the code and the hold duration used in the on-screen animation are stored as one linked value with a comment pointing each one at the other | If they ever drift apart, the fill would lie about how long the hold actually takes |
| The approval fires only when a finger or click is released while still inside the button, never on a timer counting down in the background | A timer-based approval could not be cancelled by sliding away at the last moment |

## 4. Visual language

Light theme only. A small, named set of design tokens (CSS custom properties) carries every color, so nothing is ever hand-picked per screen:

| Token | Meaning |
|---|---|
| `--brand-500`, `--brand-600` | The one brand color, used only for the logo and links |
| `--neutral-100` through `--neutral-900` | Backgrounds, borders, and body text |
| `--tier-low` (grey) | L1 to L3, read or low-risk actions |
| `--tier-l4` (amber) | Staged, reversible-ish, external actions |
| `--tier-l5` (crimson) | Staged, irreversible, or money-moving actions |
| `--tier-success` (green) | A completed, successful outcome |
| `--space-1` through `--space-6` | A fixed spacing scale, no one-off pixel values |
| `--radius-sm`, `--radius-md` | Two corner-rounding sizes, nothing else |
| `--shadow-1`, `--shadow-2` | Two soft shadow depths, used sparingly |

Type: prose uses the operating system's own plain sans-serif font, so pages load instantly and always match the reader's device. Anything that is a machine value, like a ledger hash or a token, is shown in a monospace font on a slightly sunken chip, so it visually reads as "not for humans to type."

Flourish that is allowed: a soft two-step shadow under cards, a thin colored rule under section headings, the animated hold-to-approve fill (because that fill is functional, not decorative), and a small amount of rounded-corner softness. Flourish that is refused: gradients, blur or glass effects, animated page transitions, decorative icons that do not carry information, and colored page headings.

Petasos gets its own mark, separate from anything it is drawn from. Concept: the petasos was Hermes' wide-brimmed traveling hat with small wings at the sides. The mark is a wide, shallow arc (the brim) built from a few parallel strokes, each sheared at the same angle, so the strokes can be tinted to show the five tiers as bands from grey to crimson if the mark is ever used that way. Two short wing shapes sit at the outer ends of the arc. Single flat color for the default mark, legible even shrunk to 48 pixels, monochrome before any color version.

Rough sketch (not final art):

```
        ___----+----___
    ___-------+-------___
  _///////////+///////////\_
 (  ===  ===  |  ===  ===  )
  \___________|___________/
   (wing)             (wing)
```

## 5. The site: petasos.io

Page outline, top to bottom: a hero section with one plain sentence defining what Petasos is; a three-step "try it" path (read the short case study, watch a short recording of the demo, connect your own tools to the live demo server); an embedded recording of a real terminal session; a set of small cards, one per safety rule, each answering "why does this rule exist"; a section on how the project itself was built, mostly by coding agents, linking to the running changes ledger; and finally links to the source code repository and the live demo app.

Tone rules for every page: plain language throughout, as if explaining to someone with no technical background. Every term is defined the moment it first appears. No em dashes anywhere, use commas, colons, or periods instead. No marketing superlatives: no "best," "revolutionary," or "game-changing," just plain statements of what the thing does.

## 6. Accessibility and platform notes

The hold-to-approve gesture also works from a keyboard: tabbing to the button and holding down the space bar runs the same timed fill, and releasing space either inside or outside the hold window behaves exactly like a mouse or touch hold. When the visitor's device is set to reduce motion, the fill still shows progress (as a plain, non-animated bar reaching full at the same 1.2 or 2.5 second mark) so it stays honest about timing without any moving easing curve. All text meets at least 4.5 to 1 contrast against its background. Every tappable control is at least 44 by 44 pixels. The whole app and site work cleanly at 375 pixels wide, the narrowest common phone width.

## 7. What we deliberately do not do

- No dark mode. One light theme, kept simple on purpose.
- No push notifications of any kind.
- No formatted or rendered text anywhere on the approval screen; every summary is plain text.
- No typed confirmation phrase anywhere in the app or the API. Approval is always a button or a press-and-hold, never something a person has to type out.
