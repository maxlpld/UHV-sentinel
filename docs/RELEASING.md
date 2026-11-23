# Maintainer instructions: first publication and future releases

## First publication

1. On GitHub create a repository named `uhv-sentinel` under your account. The default repository in `release_config.json` is `maxlpld/uhv-sentinel`. If you choose another name, change it **before the first release**.
2. Upload **the contents** of this folder as the repository root, including `.github/workflows/` and `.gitignore`. Do not commit any real pressure history, passwords, configurations or serial numbers.
3. With Git configured, push the source to your repository:

```bash
git init
git add .
git commit -m "Initial UHV Sentinel release"
git branch -M main
git remote add origin https://github.com/maxlpld/uhv-sentinel.git
git push -u origin main
```

4. Once CI succeeds, publish the first release:

```bash
git tag v0.0.3
git push origin v0.0.3
```

The `Publish tagged release` workflow generates **two assets**: `uhv-sentinel-release.zip` and `SHA256SUMS.txt`. The app's Options → Update button expects exactly those filenames and a published, non-prerelease GitHub Release. If the repository is private, an unauthenticated updater cannot access its release API; publish the repository or implement authenticated updates.

You can also build these assets locally:

```bash
python scripts/build_release.py
```

## Later release (e.g. 0.2.1)

1. Commit fixes and tests. Update the `__version__` string in `version.py` to `0.2.1`.
2. Run `python -m unittest discover -s tests -v` and `python scripts/build_release.py`.
3. Push your source and tag to GitHub:

```bash
git add .
git commit -m "Release v0.2.1"
git push origin main
git tag v0.2.1
git push origin v0.2.1
```

4. Confirm the release assets are visible on GitHub. Installed users can now click **Options → Update → Check for updates → Install v0.2.1**.

**Do not** republish a different ZIP under an existing version number. Release versions must increase. The check deliberately rejects same-version or older packages. User config/history files remain outside the application directory across upgrades.

## Rollback

The updater retains the previous program files in `<installation>/previous_app`. Shut down the app first; then rename `<installation>/app` aside and copy `<installation>/previous_app` back to `app`, or reinstall an older downloaded release using the installer. This only changes program code, not user recordings. Reinstallation from older source may also downgrade dependencies, so check compatibility first.

## What has (and has not) been validated

Unit tests cover simulated serial drivers and release ZIP integrity/safe paths. Actual serial cabling, EpiMax model compatibility and UHV pressure accuracy require separate lab verification. The app is not a safety-interlock system. The web dashboard binds to private LAN IPs without authentication; do not expose it to untrusted networks or public Internet.
