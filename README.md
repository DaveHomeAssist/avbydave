# avbydave.com

Published site for https://avbydave.com (AV by Dave).

Do not edit files here. [DaveHomeAssist/system-by-dave](https://github.com/DaveHomeAssist/system-by-dave)
publishes this repository on every release with `scripts/publish_domain_site.sh`; change the
source there. `docs/domain-sites.md` in that repository describes the pages, the release
gates and the cutover from systembydave.com.

Each publish replaces everything here except `.github/` and this README, and records the
source commit in `source.json`. Pushes to `main` deploy GitHub Pages.
