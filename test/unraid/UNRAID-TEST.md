# Testing the Unraid template without the app store

Community Applications (CA) shows the tag selection ("Default", `latest-cuda12`, `latest-rocm-gfx110x`, …)
only for apps from its feed. A template dropped in as a private app always installs with the default tag.
To test the selection before the app is listed in CA, this test feeds CA a local copy of its feed with
our template added.

How it works (CA source, `Squidly271/community.applications`, `include/`):

- `paths.php` has a developer switch, `$caPaths['localONLY'] = false;`. With `true`, CA reads its feed from
  `/tmp/GitHub/AppFeed/applicationFeed.json` instead of `assets.ca.unraid.net` (`DownloadApplicationFeed()`
  in `exec.php`).
- The tag selection is built from the `Branch` entries of a feed app. A branch may override any field except
  `Tag` and `TagDescription`; our AMD branches override `ExtraParams` and `Requires`.

Everything here lives in RAM on Unraid (`/usr/local/emhttp`, `/tmp`): a reboot undoes it.

## 1. Build the feed (on your PC, in the project folder)

```sh
curl -fo test/unraid/applicationFeed.orig.json https://assets.ca.unraid.net/feed/applicationFeed.json
python3 -I test/unraid/make_feed.py test/unraid/applicationFeed.orig.json test/unraid/applicationFeed.json
```

The script takes `unraid/strata-supervisor.xml` as it is, adds it to the feed and turns `latest` into `dev`
(`latest-cuda12` into `dev-cuda12` and so on). It prints the default tag and every branch. Both JSON files
are in `.gitignore`. Build the feed again after every change to the template.

## 2. Copy it to the server

```sh
ssh root@tower mkdir -p /tmp/GitHub/AppFeed
scp test/unraid/applicationFeed.json root@tower:/tmp/GitHub/AppFeed/
```

## 3. Switch CA to the local feed (on the server)

```sh
sed -i "s/\(\['localONLY'\][[:space:]]*=[[:space:]]*\)false;/\1true;/" \
  /usr/local/emhttp/plugins/community.applications/include/paths.php
grep -n localONLY /usr/local/emhttp/plugins/community.applications/include/paths.php | head -1
rm -rf /tmp/community.applications/tempFiles
```

The `grep` must show `= true;`. Removing `tempFiles` makes CA read the feed again when you open the Apps tab.

## 4. Test in the web UI

1. Open **Apps** and search for `strata`. The app shows up as from "sognix's Repository".
2. Click **Install**. The selection must list **Default** (`:dev`) and the four branches, each with its
   description.
3. Pick `dev-cuda12`: the form must show the repository `ghcr.io/sognix/strata-supervisor:dev-cuda12`
   and Extra Parameters `--gpus all --ulimit memlock=-1:-1 --shm-size 2g`. Cancel.
4. Pick `dev-rocm-gfx110x`: repository `…:dev-rocm-gfx110x`, Extra Parameters
   `--device /dev/kfd --device /dev/dri --group-add video --ulimit memlock=-1:-1 --shm-size 2g`. Cancel (no AMD card in
   the server).
5. Pick **Default** and install for real: this is the `cuda13` image for the server test.

To see a change to the template: build the feed again (step 1), copy it (step 2) and run the `rm -rf` from
step 3.

## 5. Back to normal

```sh
sed -i "s/\(\['localONLY'\][[:space:]]*=[[:space:]]*\)true;/\1false;/" \
  /usr/local/emhttp/plugins/community.applications/include/paths.php
rm -rf /tmp/community.applications/tempFiles /tmp/GitHub/AppFeed
```

Or reboot the server. While the switch is on, CA shows only the apps of the local file: no new apps and no
updates. A CA plugin update also resets the switch.

## Not covered

- The AMD branches can be checked in the form only; this server has no AMD card.
- CA's own validation of a submitted template (that happens on `ca.unraid.net/submit`).
