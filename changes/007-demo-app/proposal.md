# Proposal: 007-demo-app

status: proposed
lane: claude
depends_on: 004-owner-api
decisions: D-003, D-009, D-014, D-016, D-017

## Why

Rung 2 of PRODUCT.md's reviewer ladder is a small web app a person can watch for five minutes. Change 004 built the browser door into the gate; this change builds the app on it. (Split from the earlier `004-owner-api-and-demo-app` on 2026-09-30 so each half fits one builder run.)

## What this change delivers

A Preact and Vite demo app under `demo/`, served at `petasos.io/demo/`, led by a guided scenario: an "Ask for a $42 refund" button that has the simulated agent propose the refund, then shows why it waits, the exact approval card (amount, currency, customer, ticket version), the press-and-hold approval, the fake result, and the new ledger rows. Also: the ticket list and ticket pages with the agent's actions, an approvals inbox with press-and-hold (1.2 s for L4, 2.5 s for L5, per D-009), a quiet "N waiting for you" pill that is chrome on every screen, a ledger viewer with a "verify chain" button, and a "trip the canary" button, styled per D-014's semantic palette.

Keyboard and accessibility: holding the space bar on the focused approve control works like holding the mouse; releasing early, losing focus, or sliding off cancels the hold, and the key repeat a held key produces is ignored; a plain approve button is offered behind a disclosure wherever a hold is not usable (D-009 already allows a button). The role switcher is labelled as a simulation.

## What this change does not deliver

No new server code (004 has every endpoint the app uses), no production deployment (005 deploys the API; `deploy-site.yml` deploys this app once it exists), and no case-study prose (006). The app's tests run against recorded responses whose key sets 004's fixture-contract test checks.

## Reader test

After this merges, a person with no instructions can run the API locally, open the demo app, press "Ask for a $42 refund", read exactly what they are approving, approve it with a hold or the keyboard, and see the fake refund and its ledger rows.
