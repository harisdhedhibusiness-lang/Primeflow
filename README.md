# Monster Stadium - Native Android Cloud Build

This is a native Android project, not HTML/PWA/WebView.

## What this is
- Native Java Android app
- 1,024 original creatures
- Starter choice:
  - Tayz → Voltra → Ravolt
  - Tiko → Cindar → Flarex
  - Nero → Torran → Abyssor
  - Kairo → Thornix → Virdan
- Phase 2+ locked Future Champions
- Owner Mode
- Turn-based arena battle prototype
- XP, leveling, evolution checks
- Potions, boosts, coins, expansion pass stub

## Owner codes
Owner unlock:

INGEN-OWNER

Reset save:

RESET

## Phone-only APK build using GitHub Actions

You do not need a PC. You need a GitHub account and your phone browser.

1. On your phone, go to github.com and sign in.
2. Create a new repository.
3. Upload all files/folders from this ZIP into the repository.
4. Open the repository's Actions tab.
5. Select "Build Android APK".
6. Tap "Run workflow".
7. After it finishes, open the workflow run.
8. Download the artifact named "MonsterStadium-debug-apk".
9. Extract the artifact ZIP on your phone.
10. Install app-debug.apk.

## Why this path is needed
A real APK requires Android SDK tools. This chat environment does not include them, so the APK must be compiled by Android Studio or a cloud builder such as GitHub Actions.
