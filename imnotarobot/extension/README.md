# Captcha Solver for ImNotARobot

A browser extension that solves captchas on the pages you open through the
local [ImNotARobot](../README.md) app — no clicks, no paid service. Works in
Chrome and in Chromium-based anti-detect browsers (Dolphin Anty, AdsPower,
Octo Browser…).

Based on the 2Captcha extension; see `NOTICE` for what was changed and
`LICENSE` for the upstream MIT license.

## Install

1. Open the ImNotARobot window → **Connection** → **Browser extension** →
   *Show the folder* (or find this folder in the app bundle).
2. Chrome: open `chrome://extensions`, turn on **Developer mode**, press
   **Load unpacked** and pick this folder. In an anti-detect browser, add this
   folder to the profile as a local extension.
3. Open the extension's options (or its popup), paste the **access key** from
   the app's **Connection** section, press **Login**. The server address is
   already set to `http://127.0.0.1:8765`.

Image captchas are picked up after you once point the extension at them:
right-click the picture → *Solve this captcha*, then right-click the answer
field → *Insert the answer here*. Checkbox captchas (reCAPTCHA, hCaptcha,
Turnstile) are found by themselves.

What the app can solve — and what each step costs — is decided by the app's
captcha path, not by this extension.
